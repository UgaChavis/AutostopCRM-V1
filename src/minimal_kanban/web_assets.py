from .printing.web_module import (
    PRINTING_WEB_MODULE_HTML,
    PRINTING_WEB_MODULE_SCRIPT,
    PRINTING_WEB_MODULE_STYLE,
)

BOARD_WEB_APP_HTML = "".join(
    [
        """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
<title>AutoStop</title>
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
      --scroll-track: rgba(8, 11, 9, 0.58);
      --scroll-thumb: rgba(104, 118, 89, 0.88);
      --scroll-thumb-hover: rgba(166, 178, 129, 0.92);
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
    * {
      scrollbar-width: thin;
      scrollbar-color: var(--scroll-thumb) var(--scroll-track);
    }
    *::-webkit-scrollbar {
      width: 12px;
      height: 12px;
    }
    *::-webkit-scrollbar-track {
      background: linear-gradient(180deg, rgba(255,255,255,0.03), transparent 24%), var(--scroll-track);
      border: 1px solid rgba(73, 83, 70, 0.38);
    }
    *::-webkit-scrollbar-thumb {
      border: 1px solid rgba(182, 193, 145, 0.45);
      background:
        linear-gradient(180deg, rgba(255,255,255,0.1), transparent 35%),
        var(--scroll-thumb);
      min-height: 28px;
    }
    *::-webkit-scrollbar-thumb:hover {
      background:
        linear-gradient(180deg, rgba(255,255,255,0.14), transparent 35%),
        var(--scroll-thumb-hover);
    }
    *::-webkit-scrollbar-corner {
      background: var(--scroll-track);
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
    .column__head-actions {
      display: inline-flex;
      align-items: center;
      justify-content: flex-end;
      gap: calc(6px * var(--board-scale));
    }
    .column__title {
      font-family: var(--mono);
      font-size: calc(14px * var(--board-scale));
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      text-shadow: 0 1px 0 rgba(0,0,0,0.38);
    }
    .column__rename,
    .column__delete {
      width: calc(22px * var(--board-scale));
      height: calc(22px * var(--board-scale));
      padding: 0;
      border-color: rgba(255,255,255,0.08);
      background: rgba(0,0,0,0.14);
      color: rgba(242, 240, 230, 0.62);
      font-size: calc(11px * var(--board-scale));
      line-height: 1;
      opacity: 0;
      pointer-events: none;
      transition: opacity 120ms ease, color 120ms ease, border-color 120ms ease, background 120ms ease;
    }
    .column:hover .column__rename,
    .column:hover .column__delete,
    .column:focus-within .column__rename,
    .column:focus-within .column__delete {
      opacity: 0.76;
      pointer-events: auto;
    }
    .column__rename:hover,
    .column__rename:focus-visible {
      opacity: 1;
      border-color: rgba(110, 176, 220, 0.86);
      background: rgba(22, 51, 74, 0.56);
      color: #d4edff;
      outline: none;
    }
    .column__delete:hover,
    .column__delete:focus-visible {
      opacity: 1;
      border-color: rgba(207, 91, 75, 0.86);
      background: rgba(74, 25, 22, 0.56);
      color: #ffd2c9;
      outline: none;
    }
    .column__delete[disabled] {
      opacity: 0;
      pointer-events: none;
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
      --deadline-heat-border: var(--paper-line);
      --deadline-heat-ring: rgba(83, 191, 122, 0.08);
      --deadline-heat-glow: rgba(83, 191, 122, 0.04);
      position: relative;
      border: 1px solid var(--deadline-heat-border);
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
      transition: border-color 160ms linear, box-shadow 160ms linear;
      box-shadow:
        inset 0 4px 0 #8e9570,
        inset 0 0 0 1px rgba(255,255,255,0.08),
        0 0 0 1px var(--deadline-heat-ring),
        0 0 16px var(--deadline-heat-glow),
        0 1px 0 rgba(0,0,0,0.18);
    }
    .card.is-dragging {
      opacity: 0.56;
    }
    .card.is-drop-before::before {
      content: "";
      position: absolute;
      left: calc(8px * var(--board-scale));
      right: calc(8px * var(--board-scale));
      top: calc(-5px * var(--board-scale));
      height: 2px;
      background: var(--accent);
      box-shadow: 0 0 calc(6px * var(--board-scale)) rgba(182, 193, 145, 0.34);
      pointer-events: none;
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
    .card[data-status="expired"] {
      box-shadow:
        inset 0 4px 0 #8e9570,
        inset 0 0 0 1px rgba(255,255,255,0.08),
        0 0 0 1px var(--deadline-heat-ring),
        0 0 20px var(--deadline-heat-glow),
        0 0 32px rgba(212, 98, 98, 0.14),
        0 1px 0 rgba(0,0,0,0.18);
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
        font-size: calc(15px * var(--board-scale));
        line-height: 1.36;
        color: rgba(104, 42, 39, 0.94);
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
      display: grid;
      gap: calc(2px * var(--board-scale));
      align-content: start;
      min-width: 0;
      padding-right: calc(42px * var(--board-scale));
      font-family: var(--mono);
      text-transform: uppercase;
    }
    .card__vehicle {
      font-family: var(--mono);
      font-size: calc(15px * var(--board-scale));
      line-height: 1.08;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      color: #373227;
      font-weight: 800;
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
      font-weight: 700;
      font-size: calc(14px * var(--board-scale));
      line-height: 1.1;
      text-transform: uppercase;
      font-family: var(--mono);
      min-width: 0;
      overflow: hidden;
      white-space: nowrap;
      text-overflow: ellipsis;
      color: #454034;
    }
    .card__unread-badge,
    .card__updated-badge {
      position: absolute;
      top: calc(10px * var(--board-scale));
      right: calc(10px * var(--board-scale));
      min-width: calc(28px * var(--board-scale));
      height: calc(18px * var(--board-scale));
      padding: 0 calc(6px * var(--board-scale));
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-family: var(--mono);
      font-size: calc(9px * var(--board-scale));
      letter-spacing: 0.08em;
      text-transform: uppercase;
      pointer-events: none;
    }
    .card__unread-badge {
      border: 1px solid rgba(162, 42, 42, 0.96);
      background:
        linear-gradient(180deg, rgba(122, 20, 20, 0.96), rgba(88, 10, 10, 0.96));
      color: #fff1ee;
      box-shadow:
        inset 0 1px 0 rgba(255,255,255,0.08),
        0 0 0 1px rgba(38, 8, 8, 0.34),
        0 0 calc(8px * var(--board-scale)) rgba(120, 16, 16, 0.26);
    }
    .card__updated-badge {
      border: 1px solid rgba(198, 152, 43, 0.96);
      background:
        linear-gradient(180deg, rgba(120, 91, 16, 0.96), rgba(91, 66, 9, 0.96));
      color: #fff4c9;
      box-shadow:
        inset 0 1px 0 rgba(255,255,255,0.08),
        0 0 0 1px rgba(61, 43, 7, 0.34),
        0 0 calc(8px * var(--board-scale)) rgba(168, 126, 21, 0.2);
    }
    .card__desc {
      font-size: calc(12px * var(--board-scale));
      line-height: 1.22;
      display: -webkit-box;
      -webkit-line-clamp: 8;
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
      display: inline-flex;
      align-items: center;
      gap: calc(4px * var(--board-scale));
    }
    .tag[data-tag-color="green"] {
      border-color: rgba(67, 126, 79, 0.82);
      background: rgba(111, 173, 116, 0.52);
      color: #1d1a14;
    }
    .tag[data-tag-color="yellow"] {
      border-color: rgba(152, 126, 48, 0.82);
      background: rgba(193, 162, 84, 0.42);
      color: #1d1a14;
    }
    .tag[data-tag-color="red"] {
      border-color: rgba(152, 86, 78, 0.82);
      background: rgba(193, 118, 110, 0.42);
      color: #1d1a14;
    }
    .tag__dot {
      width: calc(8px * var(--board-scale));
      height: calc(8px * var(--board-scale));
      border-radius: 50%;
      background: currentColor;
      flex: 0 0 auto;
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
    #repairOrdersModal {
      background: rgba(7, 10, 8, 0.84);
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
    .dialog--card {
      width: min(1240px, calc(100% - 34px));
      padding: 18px;
      transform: none;
    }
    .dialog__head, .dialog__foot, .dialog__tabs {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }
""",
        r"""
    .dialog__title {
      font-family: var(--mono);
      text-transform: uppercase;
      letter-spacing: 0.14em;
      font-size: 14px;
      font-weight: 700;
    }
    .dialog__head--card {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      align-items: start;
    }
    .dialog__title-wrap {
      min-width: 0;
      display: grid;
      gap: 5px;
    }
    .dialog__title-prefix {
      font-family: var(--mono);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.16em;
      color: var(--text-soft);
    }
    .dialog__title--card {
      min-width: 0;
      max-width: 100%;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      line-height: 1.2;
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
    .overview-layout {
      display: grid;
      grid-template-columns: minmax(640px, 760px) minmax(286px, 338px);
      gap: 26px;
      align-items: start;
      justify-content: center;
    }
    .overview-main {
      display: grid;
      gap: 12px;
      min-width: 0;
      max-width: 760px;
      position: relative;
      z-index: 1;
    }
    .overview-main__meta {
      display: grid;
      grid-template-columns: minmax(136px, 172px) minmax(0, 1fr);
      gap: 8px;
      align-items: start;
    }
    .stack { display: grid; gap: 12px; }
    .field { display: grid; gap: 6px; }
    .grid--overview { grid-template-columns: minmax(170px, 0.64fr) minmax(0, 1fr); gap: 10px; }
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
    input[type="text"], input[type="password"], textarea, select, input[type="number"] {
      width: 100%;
      border: 1px solid var(--line);
      background: #151c17;
      color: var(--text);
      color-scheme: dark;
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
    .field--compact input[type="password"],
    .field--compact select {
      min-height: 34px;
      padding: 6px 8px;
      font-size: 13px;
    }
    input:-webkit-autofill,
    input:-webkit-autofill:hover,
    input:-webkit-autofill:focus,
    input:-webkit-autofill:active {
      -webkit-text-fill-color: var(--text);
      -webkit-box-shadow: 0 0 0 1000px #151c17 inset;
      box-shadow: 0 0 0 1000px #151c17 inset;
      caret-color: var(--text);
    }
    input[type="number"] { text-align: center; }
    textarea { min-height: 192px; }
    .field--description textarea {
      min-height: 168px;
      height: 168px;
      max-height: clamp(440px, 56vh, 720px);
      padding: 12px 13px;
      line-height: 1.58;
      resize: vertical;
      overflow-y: auto;
    }
    .panel-title {
      font-family: var(--mono);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: var(--text-soft);
    }
    .signal-panel {
      gap: 8px;
      padding: 10px 10px 10px;
      align-content: start;
      min-width: 0;
    }
    .signal-preview {
      border: 1px solid var(--line-soft);
      background:
        linear-gradient(180deg, rgba(255,255,255,0.03), transparent 38%),
        rgba(0,0,0,0.18);
      min-height: 34px;
      padding: 6px 10px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-family: var(--mono);
      font-size: 13px;
      line-height: 1.15;
      letter-spacing: 0.06em;
      color: var(--text);
    }
    .signal-preview .time-readout { gap: 6px; }
    .signal-preview .time-readout__unit { font-size: 0.58em; }
    .signal-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 4px;
    }
    .signal-cell {
      display: grid;
      gap: 2px;
    }
    .signal-cell span {
      font-family: var(--mono);
      font-size: 7px;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      color: var(--text-soft);
    }
    .signal-panel input[type="number"] {
      min-height: 24px;
      padding: 2px 4px;
      font-size: 11px;
    }
    .signal-grid--timer {
      gap: 8px;
    }
    .signal-grid--timer > .signal-cell:not(.signal-cell--timer) {
      display: none;
    }
    .signal-cell--timer {
      gap: 4px;
    }
    .signal-cell__label {
      font-family: var(--mono);
      font-size: 9px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--text-soft);
    }
    .signal-input {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      min-height: 36px;
      border: 1px solid var(--line-soft);
      background:
        linear-gradient(180deg, rgba(255,255,255,0.03), transparent 40%),
        rgba(0,0,0,0.16);
    }
    .signal-input__field {
      min-width: 0;
      min-height: 34px;
      border: 0;
      background: transparent;
      padding: 0 10px;
      font-family: var(--mono);
      font-size: 15px;
      font-weight: 700;
      color: var(--text);
      text-align: center;
      appearance: textfield;
      -moz-appearance: textfield;
    }
    .signal-input__field::-webkit-outer-spin-button,
    .signal-input__field::-webkit-inner-spin-button {
      -webkit-appearance: none;
      margin: 0;
    }
    .signal-input__field:focus {
      outline: none;
      background: rgba(255,255,255,0.02);
    }
    .signal-input__unit {
      min-width: 26px;
      padding: 0 8px 0 0;
      font-family: var(--mono);
      font-size: 10px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--text-soft);
      text-align: center;
      pointer-events: none;
    }
    .tags-panel {
      gap: 6px;
      padding: 7px 8px 8px;
      align-content: start;
      min-width: 0;
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
    .tags-panel {
      gap: 8px;
      padding: 10px 10px 10px;
      align-content: start;
      min-width: 0;
    }
    .tags-panel__head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }
    .tag-limit {
      min-width: 44px;
      padding: 4px 6px;
      border: 1px solid var(--line-soft);
      background: rgba(0,0,0,0.16);
      font-family: var(--mono);
      font-size: 10px;
      letter-spacing: 0.06em;
      text-align: center;
      color: var(--text-soft);
    }
    .tag-limit[data-limit-state="full"] {
      border-color: rgba(193, 162, 84, 0.48);
      color: #d7c58a;
      background: rgba(193, 162, 84, 0.12);
    }
    .tag-entry {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 40px;
      gap: 6px;
      align-items: center;
    }
    .tag-list {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      align-content: flex-start;
      min-height: 34px;
      padding: 2px 0;
    }
    .tag-entry input[type="text"] {
      min-height: 34px;
      padding: 6px 10px;
      font-family: var(--mono);
      font-size: 12px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    .tag-entry input[type="text"][disabled] {
      opacity: 0.62;
      cursor: not-allowed;
    }
    .tag-entry .btn {
      min-width: 40px;
      min-height: 34px;
      padding: 0;
      display: grid;
      place-items: center;
      font-size: 13px;
    }
    .tag-entry .btn[disabled] {
      opacity: 0.44;
      cursor: not-allowed;
    }
    .tag-suggestions {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      align-content: flex-start;
      min-height: 28px;
    }
    .tag-color-picker {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 28px;
    }
    .tag-color-option {
      width: 18px;
      height: 18px;
      border: 1px solid rgba(255,255,255,0.14);
      background: rgba(0, 0, 0, 0.16);
      padding: 0;
      cursor: pointer;
      position: relative;
    }
    .tag-color-option::after {
      content: "";
      position: absolute;
      inset: 3px;
      border-radius: 50%;
      background: currentColor;
    }
    .tag-color-option[data-tag-color="green"] { color: #3f8b52; }
    .tag-color-option[data-tag-color="yellow"] { color: #aa8a34; }
    .tag-color-option[data-tag-color="red"] { color: #b06157; }
    .tag-color-option.is-active {
      border-color: rgba(241, 239, 228, 0.78);
      box-shadow: 0 0 0 1px rgba(241, 239, 228, 0.18);
    }
    .tag-list .tag {
      display: inline-flex;
      align-items: center;
      justify-content: flex-start;
      min-height: 24px;
      padding: 3px 8px;
      font-family: var(--mono);
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.05em;
      line-height: 1;
    }
    .tag-list .tag--muted {
      justify-content: center;
      min-width: 110px;
      color: var(--text-soft);
      background: rgba(0,0,0,0.14);
      border-color: var(--line-soft);
    }
    .tag-suggestion {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 24px;
      border: 1px solid var(--line-soft);
      background: rgba(0, 0, 0, 0.16);
      color: var(--text-soft);
      padding: 3px 8px;
      font-family: var(--mono);
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      line-height: 1;
      cursor: pointer;
    }
    .tag-suggestion[data-tag-color="green"] {
      border-color: rgba(67, 126, 79, 0.62);
      background: rgba(111, 173, 116, 0.22);
      color: #1d1a14;
    }
    .tag-suggestion[data-tag-color="yellow"] {
      border-color: rgba(152, 126, 48, 0.58);
      background: rgba(193, 162, 84, 0.18);
      color: #1d1a14;
    }
    .tag-suggestion[data-tag-color="red"] {
      border-color: rgba(152, 86, 78, 0.58);
      background: rgba(193, 118, 110, 0.18);
      color: #1d1a14;
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
    .tag-suggestion.is-disabled {
      opacity: 0.42;
      cursor: not-allowed;
      filter: saturate(0.6);
    }
    .tag-suggestion.is-disabled:hover {
      color: var(--text-soft);
      border-color: var(--line-soft);
      background: rgba(0, 0, 0, 0.16);
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
    .vehicle-panel {
      gap: 8px;
      width: min(100%, 338px);
      max-width: 338px;
      justify-self: end;
      margin-left: 6px;
      padding: 9px 10px;
      position: relative;
      z-index: 2;
      isolation: isolate;
      background:
        linear-gradient(180deg, rgba(255,255,255,0.02), transparent 20%),
        rgba(0, 0, 0, 0.16);
    }
    .vehicle-panel::before {
      content: "";
      position: absolute;
      top: 12px;
      bottom: 12px;
      left: -14px;
      width: 1px;
      background: rgba(115, 126, 105, 0.24);
    }
    .vehicle-panel__head {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: start;
      gap: 10px;
    }
    .vehicle-panel__summary {
      color: var(--text-soft);
      font-family: var(--mono);
      font-size: 11px;
      line-height: 1.5;
      letter-spacing: 0.03em;
      white-space: pre-wrap;
    }
    .vehicle-panel__flags {
      display: flex;
      flex-wrap: wrap;
      gap: 3px;
    }
    .vehicle-flag {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 3px 7px;
      border: 1px solid var(--line-soft);
      background: rgba(0, 0, 0, 0.18);
      color: var(--text-soft);
      font-family: var(--mono);
      font-size: 10px;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      line-height: 1.1;
    }
    .vehicle-flag--accent {
      color: var(--text);
      border-color: var(--accent);
      background: rgba(201, 180, 118, 0.08);
    }
    .vehicle-flag--warning {
      color: #f0c1ba;
      border-color: rgba(209, 90, 76, 0.48);
      background: rgba(209, 90, 76, 0.1);
    }
    .vehicle-autofill {
      display: grid;
      gap: 8px;
      padding-top: 1px;
    }
    .vehicle-autofill__textarea {
      min-height: 58px;
      max-height: 116px;
      font-family: var(--sans);
      font-size: 13px;
      line-height: 1.45;
    }
    .vehicle-autofill__hint {
      display: none;
    }
    .vehicle-autofill__status {
      display: none;
    }
    .vehicle-autofill__status.is-warning {
      color: #ffd7d0;
      border-color: rgba(209, 90, 76, 0.48);
      background: rgba(99, 24, 18, 0.18);
    }
    .vehicle-autofill__status.is-empty {
      color: var(--text-soft);
    }
    .vehicle-panel__fields {
      display: grid;
      gap: 7px;
      max-height: none;
      overflow: visible;
      padding-right: 0;
    }
    .vehicle-panel__repair {
      display: grid;
      gap: 8px;
      padding-top: 10px;
      margin-top: 2px;
      border-top: 1px solid rgba(115, 126, 105, 0.24);
      position: relative;
      z-index: 3;
    }
    #repairOrderButton {
      position: relative;
      z-index: 4;
      pointer-events: auto;
    }
    #repairOrderModal {
      z-index: 14;
    }
    .vehicle-panel__repair-note {
      color: var(--text-soft);
      font-family: var(--mono);
      font-size: 11px;
      line-height: 1.45;
      letter-spacing: 0.03em;
    }
    .dialog--repair-order {
      width: min(1380px, calc(100% - 18px));
      max-height: min(94vh, 980px);
      padding: 0;
      gap: 0;
      overflow: hidden;
      grid-template-rows: auto minmax(0, 1fr) auto;
      background:
        linear-gradient(180deg, rgba(255,255,255,0.02), transparent 18%),
        var(--bg-panel);
    }
    .dialog--repair-order .dialog__head {
      padding: 15px 16px 11px;
      margin: 0;
      border-bottom: 1px solid rgba(115, 126, 105, 0.2);
      background: rgba(0, 0, 0, 0.08);
    }
    .repair-order-shell {
      display: grid;
      gap: 12px;
      padding: 14px 16px 16px;
      overflow: auto;
      min-height: 0;
      align-content: start;
    }
    .repair-order-toolbar {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 10px;
      flex-wrap: wrap;
    }
    .repair-order-groups {
      display: grid;
      grid-template-columns: minmax(236px, 0.82fr) minmax(332px, 1.04fr) minmax(544px, 1.72fr);
      gap: 12px;
      align-items: start;
    }
    .repair-order-card,
    .repair-order-table-card {
      display: grid;
      gap: 9px;
      padding: 11px;
      border: 1px solid rgba(116, 126, 106, 0.18);
      background:
        linear-gradient(180deg, rgba(255,255,255,0.02), transparent 24%),
        rgba(0, 0, 0, 0.08);
    }
    .repair-order-card__grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }
    .repair-order-card__grid--document {
      grid-template-columns: 56px repeat(3, minmax(0, 1fr));
      align-items: end;
    }
    .repair-order-card__grid--client {
      grid-template-columns: minmax(0, 1.82fr) minmax(162px, 0.66fr);
      align-items: end;
    }
    .repair-order-card__grid--vehicle {
      grid-template-columns: minmax(0, 1.46fr) minmax(124px, 0.54fr) minmax(0, 1.58fr) minmax(108px, 0.46fr);
      align-items: end;
    }
    .repair-order-card__grid--document .field--compact input[type="text"],
    .repair-order-card__grid--client .field--compact input[type="text"],
    .repair-order-card__grid--vehicle .field--compact input[type="text"] {
      min-height: 32px;
      padding: 5px 8px;
      font-size: 12.5px;
    }
    .repair-order-field--number input[type="text"],
    .repair-order-field--vin input[type="text"] {
      font-family: var(--mono);
      letter-spacing: 0.02em;
    }
    .repair-order-field--number input[type="text"] {
      text-align: center;
    }
    .repair-order-field--client input[type="text"],
    .repair-order-field--vehicle input[type="text"] {
      font-size: 13.5px;
    }
    .repair-order-card__grid--document .field--compact label,
    .repair-order-card__grid--vehicle .field--compact label,
    .repair-order-card__grid--client .field--compact label {
      font-size: 10px;
      letter-spacing: 0.06em;
      white-space: nowrap;
    }
    .repair-order-client-info textarea {
      min-height: 156px;
      height: 156px;
      max-height: 228px;
      line-height: 1.48;
      padding: 10px 12px;
      font-size: 13px;
    }
    .repair-order-status {
      display: inline-flex;
      align-items: center;
      justify-self: start;
      min-height: 26px;
      padding: 5px 9px;
      border: 1px solid rgba(116, 126, 106, 0.24);
      background: rgba(255, 255, 255, 0.02);
      color: var(--text);
      font-family: var(--mono);
      font-size: 10px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .repair-order-status[data-status="closed"] {
      border-color: rgba(170, 181, 139, 0.38);
      background: rgba(170, 181, 139, 0.1);
      color: #f3efde;
    }
    .repair-order-section-bar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
    }
    .repair-order-table-wrap {
      border: 1px solid rgba(116, 126, 106, 0.16);
      background: rgba(0, 0, 0, 0.07);
      overflow: hidden;
    }
    .repair-order-table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    .repair-order-table th {
      padding: 8px 10px;
      color: var(--text-soft);
      font-family: var(--mono);
      font-size: 10px;
      font-weight: 600;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      text-align: left;
      border-bottom: 1px solid rgba(116, 126, 106, 0.18);
      background: rgba(255, 255, 255, 0.02);
    }
    .repair-order-table td {
      padding: 3px 6px;
      vertical-align: middle;
      border-bottom: 1px solid rgba(116, 126, 106, 0.12);
    }
    .repair-order-table tbody tr:last-child td {
      border-bottom: none;
    }
    .repair-order-table__numeric {
      text-align: right;
    }
    .repair-order-table__action {
      width: 52px;
      text-align: center;
    }
    .repair-order-table__input {
      width: 100%;
      min-width: 0;
      border: 1px solid transparent;
      border-bottom-color: rgba(116, 126, 106, 0.16);
      background: transparent;
      color: var(--text);
      padding: 7px 8px;
      min-height: 34px;
      outline: none;
      font-size: 13px;
    }
    .repair-order-table__input:focus {
      border-bottom-color: var(--accent);
      box-shadow: inset 0 -1px 0 rgba(167, 178, 132, 0.22);
    }
    .repair-order-table__input--num {
      text-align: right;
      font-variant-numeric: tabular-nums;
      font-family: var(--mono);
    }
    .repair-order-cell-total {
      min-height: 34px;
      display: flex;
      align-items: center;
      justify-content: flex-end;
      padding: 0 9px;
      color: var(--text);
      font-family: var(--mono);
      font-size: 12.5px;
      font-variant-numeric: tabular-nums;
    }
    .repair-order-cell-total[data-empty="true"] {
      color: rgba(200, 198, 187, 0.56);
    }
    .repair-order-row-remove {
      width: 30px;
      min-width: 30px;
      height: 30px;
      padding: 0;
      font-size: 16px;
      line-height: 1;
    }
    .repair-order-subtotal {
      display: flex;
      justify-content: flex-end;
      align-items: center;
      gap: 12px;
      color: var(--text-soft);
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }
    .repair-order-subtotal strong {
      min-width: 132px;
      text-align: right;
      color: var(--text);
      font-size: 16px;
      letter-spacing: 0.02em;
      font-variant-numeric: tabular-nums;
    }
    .repair-order-footer {
      padding: 12px 16px 14px;
      margin: 0;
      border-top: 1px solid rgba(115, 126, 105, 0.18);
      background:
        linear-gradient(180deg, rgba(255,255,255,0.02), transparent 55%),
        rgba(17, 23, 19, 0.94);
      align-items: flex-end;
    }
    .repair-order-footer__totals {
      display: flex;
      align-items: flex-end;
      gap: 14px;
      flex-wrap: wrap;
    }
    .repair-order-total {
      display: grid;
      gap: 3px;
      min-width: 132px;
    }
    .repair-order-total span {
      color: var(--text-soft);
      font-family: var(--mono);
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .repair-order-total strong {
      color: var(--text);
      font-size: 18px;
      font-variant-numeric: tabular-nums;
      line-height: 1.1;
    }
    .repair-order-total--grand {
      padding: 9px 12px;
      border: 1px solid rgba(140, 151, 109, 0.34);
      background: rgba(140, 151, 109, 0.12);
    }
    .repair-order-total--grand strong {
      font-size: 28px;
      color: #f7f4e6;
    }
    .repair-order-footer__actions {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .repair-order-footer__actions .btn {
      min-width: 118px;
    }
    .repair-order-save {
      border-color: #aab58b;
      background: #aab58b;
      color: #182018;
      font-weight: 700;
    }
    .repair-order-save:hover {
      border-color: #b8c39d;
      background: #b8c39d;
    }
    .vehicle-group {
      display: grid;
      gap: 8px;
      padding-top: 9px;
      border-top: 1px solid rgba(115, 126, 105, 0.18);
    }
    .vehicle-group:first-child {
      padding-top: 0;
      border-top: none;
    }
    .vehicle-group__title {
      font-family: var(--mono);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: var(--text-soft);
    }
    .vehicle-group__grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 6px;
      align-items: start;
    }
    .vehicle-field {
      gap: 4px;
    }
    .vehicle-field input,
    .vehicle-field select {
      min-height: 34px;
      padding: 6px 8px;
      font-size: 13px;
    }
    .vehicle-field textarea {
      min-height: 56px;
      max-height: 104px;
      resize: vertical;
      font-size: 13px;
      line-height: 1.45;
    }
    .vehicle-field--wide {
      grid-column: 1 / -1;
    }
    .vehicle-field__label {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 6px;
    }
    .vehicle-field__label label,
    .vehicle-field__label span {
      font-family: var(--mono);
      font-size: 11px;
      text-transform: uppercase;
      color: var(--text-soft);
      letter-spacing: 0.06em;
    }
    .vehicle-control--mono {
      font-family: var(--mono);
      letter-spacing: 0.05em;
    }
    .vehicle-copy {
      border: 1px solid rgba(115, 126, 105, 0.28);
      background: rgba(0, 0, 0, 0.16);
      color: var(--text-soft);
      padding: 3px 6px;
      min-height: 22px;
      cursor: pointer;
      font-family: var(--mono);
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }
    .vehicle-copy:hover {
      color: var(--text);
      border-color: var(--accent);
    }
    .vehicle-suspect {
      border-color: rgba(209, 90, 76, 0.62) !important;
      box-shadow: inset 0 0 0 1px rgba(209, 90, 76, 0.15);
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
    .file-zone-panel {
      gap: 12px;
    }
    .file-dropzone {
      min-height: 136px;
      padding: 16px 18px;
      display: grid;
      place-items: center;
      text-align: center;
      border: 1px dashed rgba(167, 178, 132, 0.4);
      background:
        linear-gradient(180deg, rgba(255,255,255,0.02), transparent 34%),
        rgba(0,0,0,0.16);
      color: var(--text-soft);
      cursor: pointer;
      outline: none;
      caret-color: transparent;
      user-select: none;
      transition: border-color 120ms ease, background 120ms ease, box-shadow 120ms ease, color 120ms ease;
    }
    .file-dropzone:hover,
    .file-dropzone:focus-visible,
    .file-dropzone.is-active {
      border-color: var(--accent);
      background:
        linear-gradient(180deg, rgba(255,255,255,0.03), transparent 34%),
        rgba(167, 178, 132, 0.06);
      color: var(--text);
      box-shadow: inset 0 0 0 1px rgba(167, 178, 132, 0.16);
    }
    .file-dropzone.is-disabled {
      opacity: 0.58;
      cursor: not-allowed;
      border-style: solid;
      box-shadow: none;
    }
    .file-dropzone::before {
      content: attr(data-title);
      display: block;
      font-family: var(--mono);
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--text);
      margin-bottom: 8px;
    }
    .file-dropzone::after {
      content: attr(data-hint);
      display: block;
      max-width: 520px;
      font-size: 13px;
      line-height: 1.5;
      color: inherit;
      white-space: pre-wrap;
    }
    .file-dropzone__meta {
      color: var(--text-soft);
      font-family: var(--mono);
      font-size: 11px;
      line-height: 1.45;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    .archive-row--compact {
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      gap: 10px 12px;
    }
    .archive-row__main {
      min-width: 0;
      display: grid;
      gap: 4px;
    }
    .archive-row__title {
      font-weight: 700;
      min-width: 0;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .archive-row__summary {
      color: var(--text-soft);
      font-size: 13px;
      min-width: 0;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .archive-row__side {
      display: grid;
      justify-items: end;
      gap: 8px;
    }
    .dialog--repair-orders {
      --repair-orders-columns:
        minmax(74px, 90px)
        minmax(118px, 132px)
        minmax(118px, 132px)
        minmax(72px, 88px)
        minmax(190px, 240px)
        minmax(150px, 190px)
        minmax(420px, 1.9fr)
        minmax(88px, 110px);
    }
    .repair-orders-table-head,
    .repair-orders-row {
      display: grid;
      grid-template-columns: var(--repair-orders-columns);
      cursor: pointer;
    }
    .repair-orders-table-head {
      position: sticky;
      top: -1px;
      z-index: 2;
      cursor: default;
      gap: 8px 10px;
      padding: 8px 10px 7px;
      border: 1px solid var(--line);
      background: rgba(19, 24, 20, 0.98);
      color: var(--text-soft);
      font-family: var(--mono);
      font-size: 10px;
      line-height: 1.2;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .repair-orders-table-head__sum {
      text-align: right;
    }
    .repair-orders-row {
      align-items: stretch;
      gap: 8px 10px;
      padding: 8px 10px;
      transition: border-color 120ms ease, transform 120ms ease, background 120ms ease;
    }
    .repair-orders-row:hover {
      border-color: var(--accent);
      background: rgba(167, 178, 132, 0.05);
      transform: translateY(-1px);
    }
    .repair-orders-row__cell {
      min-width: 0;
      display: grid;
      gap: 3px;
      align-content: center;
    }
    .repair-orders-row__label {
      color: var(--text-soft);
      font-family: var(--mono);
      font-size: 10px;
      line-height: 1.2;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .repair-orders-row__number,
    .repair-orders-row__status,
    .repair-orders-row__opened,
    .repair-orders-row__closed,
    .repair-orders-row__client,
    .repair-orders-row__vehicle,
    .repair-orders-row__title,
    .repair-orders-row__total {
      min-width: 0;
      font-family: var(--mono);
      font-size: 13px;
      line-height: 1.35;
    }
    .repair-orders-row__number {
      color: var(--text);
      font-weight: 700;
    }
    .repair-orders-row__status {
      color: #f0ecdc;
    }
    .repair-orders-row__status[data-status="closed"] {
      color: #d9d3b4;
    }
    .repair-orders-row__opened,
    .repair-orders-row__closed {
      color: var(--text-soft);
      white-space: nowrap;
    }
    .repair-orders-row__client,
    .repair-orders-row__vehicle,
    .repair-orders-row__title {
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .repair-orders-row__client,
    .repair-orders-row__vehicle {
      color: var(--text-soft);
      white-space: nowrap;
    }
    .repair-orders-row__title {
      color: var(--text);
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      white-space: normal;
      word-break: break-word;
    }
    .repair-orders-row__tags {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
      align-content: flex-start;
    }
    .repair-orders-row__tags .tag {
      padding: 2px 6px;
      font-size: 9px;
    }
    .repair-orders-row__status {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: fit-content;
      min-height: 22px;
      padding: 0 8px;
      border: 1px solid rgba(167, 178, 132, 0.38);
      background: rgba(0, 0, 0, 0.16);
      font-size: 11px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    .repair-orders-row__total-cell {
      justify-items: end;
      text-align: right;
    }
    .repair-orders-row__total {
      color: #f0ecdc;
      font-size: 14px;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }
    .repair-orders-row__total[data-empty="true"] {
      color: var(--text-soft);
    }
    @media (max-width: 980px) {
      .repair-orders-table-head {
        display: none;
      }
      .dialog--repair-orders {
        --repair-orders-columns: repeat(3, minmax(0, 1fr));
      }
      .repair-orders-row__title-cell {
        grid-column: 1 / -1;
      }
      .repair-orders-row__total-cell {
        justify-items: start;
        text-align: left;
      }
    }
    @media (max-width: 760px) {
      .dialog--repair-orders {
        --repair-orders-columns: repeat(2, minmax(0, 1fr));
      }
      .repair-orders-row__total-cell,
      .repair-orders-row__title-cell {
        grid-column: 1 / -1;
        justify-items: start;
        text-align: left;
      }
    }
    @media (max-width: 560px) {
      .dialog--repair-orders {
        --repair-orders-columns: 1fr;
      }
      .repair-orders-row__total-cell,
      .repair-orders-row__title-cell {
        grid-column: auto;
      }
    }
    .dialog--repair-orders {
      width: min(1580px, calc(100% - 18px));
      max-height: min(94vh, 980px);
    }
    .repair-orders-controls {
      display: grid;
      grid-template-columns: minmax(280px, 1fr) repeat(2, minmax(180px, 220px));
      gap: 10px 12px;
      align-items: end;
    }
    .repair-orders-controls .field {
      min-width: 0;
    }
    .repair-orders-list {
      display: grid;
      gap: 6px;
      min-height: 72px;
    }
    .repair-order-tags-card {
      gap: 10px;
    }
    .repair-order-tags-card .tag-list {
      min-height: 28px;
    }
    .repair-order-tag-list {
      gap: 6px;
    }
    .repair-order-tag-item {
      display: inline-flex;
      align-items: center;
      gap: 4px;
    }
    .repair-order-tag-edit {
      cursor: pointer;
    }
    .repair-order-tag-remove {
      width: 22px;
      min-width: 22px;
      min-height: 22px;
      padding: 0;
      display: inline-grid;
      place-items: center;
      font-size: 11px;
      line-height: 1;
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
    .wall-view[data-wall-view="event_log"] {
      background: rgba(0,0,0,0.22);
      line-height: 1.58;
      overflow-wrap: anywhere;
    }
    .hidden { display: none !important; }
    .message {
      padding: 10px 12px;
      border: 1px solid var(--line);
      background: rgba(0,0,0,0.18);
      font-size: 13px;
      margin: 0;
    }
    .operator-stats-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 14px;
    }
    .operator-stat {
      border: 1px solid var(--line);
      background: rgba(0, 0, 0, 0.14);
      padding: 14px;
      display: grid;
      gap: 6px;
    }
    .operator-stat__label {
      color: var(--muted);
      font-size: 11px;
      letter-spacing: 0.08em;
    }
    .operator-stat__value {
      font-size: 28px;
      line-height: 1;
      color: var(--text);
    }
    .operator-admin-layout {
      display: grid;
      grid-template-columns: minmax(280px, 320px) minmax(0, 1fr);
      gap: 14px;
    }
    .operator-user-row {
      border: 1px solid var(--line);
      background: rgba(0, 0, 0, 0.12);
      padding: 12px;
      display: grid;
      gap: 8px;
      margin-bottom: 10px;
    }
    .operator-user-row__head,
    .operator-user-row__stats,
    .operator-user-row__actions {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
    }
    .operator-user-row__stats {
      justify-content: flex-start;
    }
    .operator-user-chip {
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.03);
      padding: 4px 8px;
      font-size: 12px;
      color: var(--muted);
    }
    @media (max-width: 1180px) {
      .dialog--card {
        width: min(1180px, calc(100% - 24px));
      }
      .overview-layout {
        grid-template-columns: minmax(0, 1fr) minmax(270px, 320px);
        gap: 18px;
      }
    }
    @media (max-width: 900px) {
      .grid { grid-template-columns: 1fr; }
      .overview-layout { grid-template-columns: 1fr; }
      .overview-main__meta { grid-template-columns: 1fr; }
      .operator-stats-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .operator-admin-layout { grid-template-columns: 1fr; }
      .vehicle-group__grid { grid-template-columns: 1fr; }
      .vehicle-panel { max-width: none; width: 100%; margin-left: 0; }
      .vehicle-panel::before { display: none; }
      .vehicle-panel__fields { max-height: none; }
      .dialog--card { width: min(1120px, 100%); }
      .dialog--repair-order { width: min(1260px, 100%); }
      .repair-order-groups { grid-template-columns: 1fr; }
      .repair-order-card__grid { grid-template-columns: 1fr; }
      .repair-order-footer { align-items: stretch; }
      .repair-order-footer__actions { width: 100%; }
      .repair-order-footer__actions .btn { flex: 1 1 0; min-width: 0; }
      .signal-grid { grid-template-columns: repeat(2, 1fr); }
      .column { width: 336px; min-width: 336px; }
    }
    @media (hover: none) {
      .column__rename,
      .column__delete {
        opacity: 0.58;
        pointer-events: auto;
      }
      .column__delete[disabled] {
        opacity: 0.24;
      }
    }
""",
        PRINTING_WEB_MODULE_STYLE,
        """
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
        <div class="brand__title">AUTOSTOP / ПУЛЬТ</div>
          <div class="brand__sub">МИНИМУМ ИНТЕРФЕЙСА · ПОЛНЫЙ ЖУРНАЛ · ХОСТ В СЕТИ</div>
        </div>
      </div>
      <div class="topbar__actions">
        <button class="btn" id="operatorButton">ОПЕРАТОР</button>
        <button class="btn" id="archiveButton">АРХИВ</button>
        <button class="btn" id="repairOrdersButton">ЗАКАЗ-НАРЯДЫ</button>
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

  <div class="modal" id="operatorProfileModal">
    <div class="dialog" style="width:min(920px,100%)">
      <div class="dialog__head">
        <div class="dialog__title">ПРОФИЛЬ ОПЕРАТОРА</div>
        <div style="display:flex; gap:8px; flex-wrap:wrap;">
          <button class="btn btn--ghost hidden" id="operatorAdminButton">АДМИН-ПАНЕЛЬ</button>
          <button class="btn" id="operatorLogoutButton">ВЫЙТИ</button>
          <button class="btn" data-close="operator-profile">ЗАКРЫТЬ</button>
        </div>
      </div>
      <div class="wall-meta" id="operatorProfileMeta">ЗАГРУЗКА ПРОФИЛЯ...</div>
      <div class="message hidden" id="operatorSecurityNotice"></div>
      <div class="operator-stats-grid" id="operatorStatsGrid"></div>
      <div class="subpanel">
        <div class="panel-title">ПОСЛЕДНИЕ ДЕЙСТВИЯ</div>
        <div class="log-view" id="operatorActivityList"></div>
      </div>
    </div>
  </div>

  <div class="modal" id="operatorAdminModal">
    <div class="dialog" style="width:min(1120px,100%)">
      <div class="dialog__head">
        <div class="dialog__title">АДМИН-ПАНЕЛЬ</div>
        <button class="btn" data-close="operator-admin">ЗАКРЫТЬ</button>
      </div>
      <div class="operator-admin-layout">
        <div class="subpanel">
          <div class="panel-title">ПОЛЬЗОВАТЕЛЬ</div>
          <div class="field field--compact">
            <label for="adminUserLogin">ЛОГИН</label>
            <input id="adminUserLogin" type="text" maxlength="40" placeholder="OPERATOR">
          </div>
          <div class="field field--compact">
            <label for="adminUserPassword">ПАРОЛЬ</label>
            <input id="adminUserPassword" type="password" maxlength="120" placeholder="Минимум 4 символа">
          </div>
          <div class="dialog__foot" style="padding:0; border:none; margin-top:10px;">
            <div class="log-row__meta">Администратор создает пользователя или обновляет ему пароль.</div>
            <button class="btn btn--accent" id="adminSaveUserButton">СОХРАНИТЬ ПОЛЬЗОВАТЕЛЯ</button>
          </div>
        </div>
        <div class="subpanel">
          <div class="panel-title">ПОЛЬЗОВАТЕЛИ</div>
          <div id="adminUsersList"></div>
        </div>
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
        <div class="dialog__title">АРХИВ / ПОСЛЕДНИЕ 30</div>
        <button class="btn" data-close="archive">ЗАКРЫТЬ</button>
      </div>
      <div id="archiveList"></div>
    </div>
  </div>

  <div class="modal" id="repairOrdersModal">
    <div class="dialog dialog--repair-orders">
      <div class="dialog__head">
        <div class="dialog__title">ЗАКАЗ-НАРЯДЫ</div>
        <button class="btn" data-close="repair-orders">ЗАКРЫТЬ</button>
      </div>
      <div class="dialog__tabs dialog__tabs--repair-orders">
        <div>
          <button class="tab-btn is-active" id="repairOrdersOpenTab" data-repair-orders-status="open">ОТКРЫТЫЕ</button>
          <button class="tab-btn" id="repairOrdersClosedTab" data-repair-orders-status="closed">АРХИВ</button>
        </div>
      </div>
      <div class="repair-orders-controls">
        <div class="field field--compact">
          <label for="repairOrdersSearchInput">ПОИСК</label>
          <input id="repairOrdersSearchInput" type="text" maxlength="120" placeholder="номер, владелец, телефон, авто, смысл">
        </div>
        <div class="field field--compact">
          <label for="repairOrdersSortBy">СОРТИРОВКА</label>
          <select id="repairOrdersSortBy">
            <option value="opened_at">Дата открытия</option>
            <option value="closed_at">Дата закрытия</option>
            <option value="number">Номер</option>
          </select>
        </div>
        <div class="field field--compact">
          <label for="repairOrdersSortDir">ПОРЯДОК</label>
          <select id="repairOrdersSortDir">
            <option value="desc">Сначала новые</option>
            <option value="asc">Сначала старые</option>
          </select>
        </div>
      </div>
      <div class="wall-meta" id="repairOrdersMeta">ЗАГРУЗКА СПИСКА...</div>
      <div class="repair-orders-table-head" id="repairOrdersTableHead">
        <div>Номер</div>
        <div>Открыта</div>
        <div>Закрыта</div>
        <div>Статус</div>
        <div>Клиент</div>
        <div>Автомобиль</div>
        <div>Смысл карточки</div>
        <div class="repair-orders-table-head__sum">Сумма</div>
      </div>
      <div class="repair-orders-list" id="repairOrdersList"></div>
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
      <div class="dialog__tabs">
        <div>
          <button class="tab-btn is-active" id="gptWallBoardTab" data-wall-view="board_content">СОДЕРЖАНИЕ ДОСКИ</button>
          <button class="tab-btn" id="gptWallEventsTab" data-wall-view="event_log">ЖУРНАЛ СОБЫТИЙ</button>
        </div>
      </div>
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
    <div class="dialog dialog--card">
      <div class="dialog__head dialog__head--card">
        <div class="dialog__title-wrap">
          <div class="dialog__title-prefix">КАРТОЧКА</div>
          <div class="dialog__title dialog__title--card" id="cardModalTitle">РАБОЧАЯ КАРТОЧКА</div>
        </div>
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
          <div class="overview-main">
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
            <div class="field field--description">
              <label for="cardDescription">ОПИСАНИЕ</label>
        <textarea id="cardDescription" maxlength="20000"></textarea>
            </div>
            <div class="overview-main__meta">
                <div class="subpanel signal-panel">
                  <div class="panel-title">ОБРАТНЫЙ ОТСЧЁТ</div>
                <div class="signal-preview" id="signalPreview">01Д 00Ч</div>
                <div class="signal-grid signal-grid--timer">
                  <label class="signal-cell signal-cell--timer"><span class="signal-cell__label">&#1044;&#1085;&#1077;&#1081;</span><div class="signal-input"><input class="signal-input__field" id="signalDaysStyled" type="number" min="0" max="365" inputmode="numeric"><span class="signal-input__unit">&#1076;</span></div></label>
                  <label class="signal-cell signal-cell--timer"><span class="signal-cell__label">&#1063;&#1072;&#1089;&#1086;&#1074;</span><div class="signal-input"><input class="signal-input__field" id="signalHoursStyled" type="number" min="0" max="23" inputmode="numeric"><span class="signal-input__unit">&#1095;</span></div></label>
                  <label class="signal-cell"><span>ДН</span><input id="signalDays" type="number" min="0" max="365"></label>
                  <label class="signal-cell"><span>ЧС</span><input id="signalHours" type="number" min="0" max="23"></label>
                </div>
              </div>
              <div class="subpanel tags-panel">
                <div class="field field--tags">
                  <div class="tags-panel__head">
                    <label for="tagInput">МЕТКИ</label>
                    <div class="tag-limit" id="tagMeta">0 / 3</div>
                  </div>
                  <div class="tag-list" id="tagList"></div>
                  <div class="tag-suggestions" id="tagSuggestions"></div>
                  <div class="tag-color-picker" id="tagColorPicker"></div>
                  <div class="tag-entry">
                    <input id="tagInput" type="text" maxlength="24" placeholder="ЖДЁМ">
                    <button class="btn" id="tagAddButton">+</button>
                  </div>
                </div>
              </div>
            </div>
          </div>
          <aside class="subpanel vehicle-panel">
            <div class="vehicle-panel__head">
              <div class="stack" style="gap:6px;">
                <div class="panel-title">ПАСПОРТ АВТОМОБИЛЯ</div>
                <div class="vehicle-panel__summary" id="vehiclePanelSummary">ТЕХКАРТА ЕЩЁ НЕ ЗАПОЛНЕНА.</div>
              </div>
              <button class="btn btn--ghost" id="vehicleAutofillButton">АВТОЗАПОЛНИТЬ</button>
            </div>
            <div class="vehicle-panel__fields" id="vehicleProfileFields"></div>
            <div class="vehicle-panel__repair">
              <button class="btn" id="repairOrderButton" data-open-repair-order-modal="true" type="button">ЗАКАЗ-НАРЯД</button>
            </div>
          </aside>
        </div>
      </section>
      <section data-panel="files" class="hidden">
        <div class="subpanel file-zone-panel">
          <div class="file-dropzone" id="fileDropzone" tabindex="0" contenteditable="plaintext-only" spellcheck="false" data-title="ПЕРЕНЕСИТЕ ИЛИ ВСТАВЬТЕ ФАЙЛ" data-hint="Ctrl+V, правый клик -> Вставить, drag-and-drop или клик для выбора. TXT, PDF, Word, Excel." aria-label="Поле для вставки и переноса файлов"></div>
          <div class="file-dropzone__meta" id="fileDropMeta">Сначала сохраните карточку, затем добавляйте вложения.</div>
          <div class="file-upload-legacy" hidden>
          <input id="fileInput" type="file" multiple hidden accept=".txt,.pdf,.doc,.docx,.xls,.xlsx,text/plain,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet">
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

  <div class="modal" id="repairOrderModal">
    <div class="dialog dialog--repair-order">
      <div class="dialog__head dialog__head--card">
        <div class="dialog__title-wrap">
          <div class="dialog__title dialog__title--card" id="repairOrderModalTitle">ЗАКАЗ-НАРЯД</div>
        </div>
        <button class="btn" data-close="repair-order">ЗАКРЫТЬ</button>
      </div>
      <div class="repair-order-shell">
        <div class="repair-order-toolbar">
          <button class="btn btn--ghost" id="repairOrderAutofillButton" type="button">АВТОЗАПОЛНЕНИЕ</button>
        </div>
        <div class="repair-order-groups">
          <section class="repair-order-card" data-repair-order-section="document">
            <div class="panel-title">ДОКУМЕНТ</div>
            <div class="repair-order-card__grid repair-order-card__grid--document">
              <div class="field field--compact repair-order-field repair-order-field--number">
                <label for="repairOrderNumber">НОМЕР</label>
                <input id="repairOrderNumber" data-repair-order-field="number" type="text" maxlength="32" placeholder="1">
              </div>
              <div class="field field--compact repair-order-field repair-order-field--date">
                <label for="repairOrderDate">ДАТА</label>
                <input id="repairOrderDate" data-repair-order-field="date" type="text" maxlength="32" placeholder="04.04.26 14:30">
              </div>
              <div class="field field--compact repair-order-field repair-order-field--date">
                <label for="repairOrderOpenedAt">ОТКРЫТА</label>
                <input id="repairOrderOpenedAt" data-repair-order-field="opened_at" type="text" maxlength="32" placeholder="05.04.26 10:30">
              </div>
              <div class="field field--compact repair-order-field repair-order-field--date">
                <label for="repairOrderClosedAt">ЗАКРЫТА</label>
                <input id="repairOrderClosedAt" data-repair-order-field="closed_at" type="text" maxlength="32" placeholder="05.04.26 18:20">
              </div>
            </div>
            <div class="repair-order-status" id="repairOrderStatus">Открыт</div>
          </section>
          <section class="repair-order-card" data-repair-order-section="client">
            <div class="panel-title">КЛИЕНТ</div>
            <div class="repair-order-card__grid repair-order-card__grid--client">
              <div class="field field--compact repair-order-field repair-order-field--client">
                <input id="repairOrderClient" data-repair-order-field="client" aria-label="Клиент" type="text" maxlength="120" placeholder="Имя и фамилия">
              </div>
              <div class="field field--compact repair-order-field repair-order-field--phone">
                <label for="repairOrderPhone">ТЕЛЕФОН</label>
                <input id="repairOrderPhone" data-repair-order-field="phone" type="text" maxlength="60" placeholder="+7 900 123-45-67">
              </div>
            </div>
          </section>
          <section class="repair-order-card" data-repair-order-section="vehicle">
            <div class="panel-title">АВТОМОБИЛЬ</div>
            <div class="repair-order-card__grid repair-order-card__grid--vehicle">
              <div class="field field--compact repair-order-field repair-order-field--vehicle">
                <input id="repairOrderVehicle" data-repair-order-field="vehicle" aria-label="Автомобиль" type="text" maxlength="120" placeholder="Volkswagen Tiguan">
              </div>
              <div class="field field--compact repair-order-field repair-order-field--plate">
                <label for="repairOrderLicensePlate">ГОСНОМЕР</label>
                <input id="repairOrderLicensePlate" data-repair-order-field="license_plate" type="text" maxlength="20" placeholder="А123АА124">
              </div>
              <div class="field field--compact repair-order-field repair-order-field--vin">
                <label for="repairOrderVin">VIN</label>
                <input id="repairOrderVin" data-repair-order-field="vin" type="text" maxlength="32" placeholder="WAUZZZ...">
              </div>
              <div class="field field--compact repair-order-field repair-order-field--mileage">
                <label for="repairOrderMileage">ПРОБЕГ</label>
                <input id="repairOrderMileage" data-repair-order-field="mileage" type="text" maxlength="32" placeholder="215 000">
              </div>
            </div>
          </section>
        </div>
        <section class="repair-order-card repair-order-card--wide hidden" data-repair-order-section="reason" aria-hidden="true">
          <div class="panel-title">ПРИЧИНА ОБРАЩЕНИЯ</div>
          <div class="field field--compact repair-order-client-info">
            <textarea id="repairOrderReason" data-repair-order-field="reason" aria-label="Причина обращения" maxlength="4000" placeholder="Кратко зафиксируйте суть обращения клиента."></textarea>
          </div>
        </section>
        <section class="repair-order-card repair-order-card--wide" data-repair-order-section="client_information">
          <div class="panel-title">ИНФОРМАЦИЯ ДЛЯ КЛИЕНТА</div>
          <div class="field field--compact repair-order-client-info">
            <textarea id="repairOrderComment" data-repair-order-field="client_information" aria-label="Информация для клиента" maxlength="4000" placeholder="Краткая история ремонта для клиента: что проверили, что нашли, что сделали и что рекомендовано дальше."></textarea>
          </div>
        </section>
        <section class="repair-order-card repair-order-card--wide hidden" data-repair-order-section="note" aria-hidden="true">
          <div class="panel-title">ПРИМЕЧАНИЕ</div>
          <div class="field field--compact repair-order-client-info">
            <textarea id="repairOrderNote" data-repair-order-field="note" aria-label="Примечание мастера" maxlength="4000" placeholder="Внутренний комментарий мастера или примечание по заказ-наряду."></textarea>
          </div>
        </section>
        <section class="repair-order-card repair-order-card--wide repair-order-tags-card hidden" data-repair-order-section="tags" aria-hidden="true">
          <div class="repair-order-section-bar">
            <div class="panel-title">ВНУТРЕННИЕ МЕТКИ</div>
            <div class="tag-limit" id="repairOrderTagMeta">0 / 5</div>
          </div>
          <div class="tag-color-picker" id="repairOrderTagColorPicker"></div>
          <div class="tag-list repair-order-tag-list" id="repairOrderTagList"></div>
          <div class="tag-entry">
            <input id="repairOrderTagInput" type="text" maxlength="24" placeholder="МЕТКА">
            <button class="btn" id="repairOrderTagAddButton" type="button">+</button>
          </div>
        </section>
        <section class="repair-order-table-card" data-repair-order-section="works">
          <div class="repair-order-section-bar">
            <div class="panel-title">РАБОТЫ</div>
            <button class="btn btn--ghost" id="repairOrderAddWorkRowButton" type="button" data-add-repair-order-row="works">+ СТРОКА</button>
          </div>
          <div class="repair-order-table-wrap">
            <table class="repair-order-table">
              <thead>
                <tr>
                  <th style="width:52%;">Наименование</th>
                  <th class="repair-order-table__numeric" style="width:12%;">Кол-во</th>
                  <th class="repair-order-table__numeric" style="width:16%;">Цена</th>
                  <th class="repair-order-table__numeric" style="width:16%;">Сумма</th>
                  <th class="repair-order-table__action" style="width:4%;"></th>
                </tr>
              </thead>
              <tbody id="repairOrderWorksBody"></tbody>
            </table>
          </div>
          <div class="repair-order-subtotal"><span>ИТОГО РАБОТЫ</span><strong data-repair-order-total="works">0,00</strong></div>
        </section>
        <section class="repair-order-table-card" data-repair-order-section="materials">
          <div class="repair-order-section-bar">
            <div class="panel-title">МАТЕРИАЛЫ</div>
            <button class="btn btn--ghost" id="repairOrderAddMaterialRowButton" type="button" data-add-repair-order-row="materials">+ СТРОКА</button>
          </div>
          <div class="repair-order-table-wrap">
            <table class="repair-order-table">
              <thead>
                <tr>
                  <th style="width:52%;">Наименование</th>
                  <th class="repair-order-table__numeric" style="width:12%;">Кол-во</th>
                  <th class="repair-order-table__numeric" style="width:16%;">Цена</th>
                  <th class="repair-order-table__numeric" style="width:16%;">Сумма</th>
                  <th class="repair-order-table__action" style="width:4%;"></th>
                </tr>
              </thead>
              <tbody id="repairOrderMaterialsBody"></tbody>
            </table>
          </div>
          <div class="repair-order-subtotal"><span>ИТОГО МАТЕРИАЛЫ</span><strong data-repair-order-total="materials">0,00</strong></div>
        </section>
      </div>
      <div class="dialog__foot repair-order-footer">
        <div class="repair-order-footer__totals">
          <div class="repair-order-total">
            <span>ИТОГО РАБОТЫ</span>
            <strong data-repair-order-total="works">0,00</strong>
          </div>
          <div class="repair-order-total">
            <span>ИТОГО МАТЕРИАЛЫ</span>
            <strong data-repair-order-total="materials">0,00</strong>
          </div>
          <div class="repair-order-total repair-order-total--grand">
            <span>ИТОГО К ОПЛАТЕ</span>
            <strong data-repair-order-total="grand">0,00</strong>
          </div>
        </div>
        <div class="repair-order-footer__actions">
          <button class="btn" data-close="repair-order">ОТМЕНА</button>
          <button class="btn btn--ghost" id="repairOrderCloseButton" type="button">ЗАКРЫТЬ ЗАКАЗ-НАРЯД</button>
          <button class="btn repair-order-save" id="repairOrderSaveButton" type="button">СОХРАНИТЬ</button>
          <button class="btn btn--ghost" id="repairOrderPrintButton" type="button">РАСПЕЧАТАТЬ</button>
        </div>
      </div>
    </div>
  </div>

""",
        PRINTING_WEB_MODULE_HTML,
        """
  <script>
    // Legacy actor localStorage flow removed in favor of operator sessions.
    const OPERATOR_SESSION_STORAGE_KEY = 'kanban-operator-session';
    const API_TOKEN_STORAGE_KEY = 'kanban-api-token';

    const state = {
      actor: '',
      operatorSessionToken: localStorage.getItem(OPERATOR_SESSION_STORAGE_KEY) || '',
      operatorProfile: null,
      operatorUsers: [],
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
      gptWallView: 'board_content',
      activeCard: null,
      editingId: null,
      currentTab: 'overview',
      vehicleProfileDraft: null,
      vehicleProfileBaseline: null,
      vehicleAutofillResult: null,
      draftTags: [],
      draftTagColor: 'green',
      pollHandle: null,
      refreshInFlight: null,
      boardDragCardId: '',
      boardDropColumnId: '',
      boardDropBeforeCardId: '',
      unreadHoverTimers: new Map(),
      unreadSeenInFlight: new Set(),
      repairOrdersFilter: 'open',
      repairOrdersQuery: '',
      repairOrdersSortBy: 'opened_at',
      repairOrdersSortDir: 'desc',
      repairOrdersLoadTimer: null,
      repairOrderTags: [],
      repairOrderTagColor: 'green',
    };

    const SNAPSHOT_POLL_INTERVAL_MS = 5000;
    const SNAPSHOT_POLL_HIDDEN_INTERVAL_MS = 15000;
    const CARD_UNREAD_HOVER_DELAY_MS = 260;

    const COLUMN_TONES = [
      { tint: 'rgba(38, 47, 41, 0.95)', head: 'rgba(95, 109, 86, 0.22)', edge: '#70796c', empty: 'rgba(167, 178, 132, 0.06)' },
      { tint: 'rgba(35, 41, 45, 0.95)', head: 'rgba(108, 116, 132, 0.18)', edge: '#6f7880', empty: 'rgba(147, 161, 176, 0.06)' },
      { tint: 'rgba(45, 43, 37, 0.95)', head: 'rgba(129, 114, 84, 0.18)', edge: '#827763', empty: 'rgba(181, 156, 107, 0.06)' },
      { tint: 'rgba(40, 44, 39, 0.95)', head: 'rgba(110, 122, 96, 0.16)', edge: '#76806f', empty: 'rgba(160, 174, 135, 0.05)' },
    ];
    const SUGGESTED_TAGS = [
      { label: 'СРОЧНО', color: 'red' },
      { label: 'ГОРИТ СРОК', color: 'yellow' },
      { label: 'ЖДЁМ', color: 'yellow' },
      { label: 'СОГЛАСОВАТЬ', color: 'green' },
      { label: 'ЗАКАЗАТЬ', color: 'green' },
    ];
    const CARD_TAG_LIMIT = 3;
    const REPAIR_ORDER_TAG_LIMIT = 5;
    const REPAIR_ORDER_SORT_FIELDS = ['number', 'opened_at', 'closed_at'];
    const REPAIR_ORDER_SORT_DIRECTIONS = ['asc', 'desc'];
    const TAG_COLOR_OPTIONS = [
      { value: 'green', label: 'Зелёная' },
      { value: 'yellow', label: 'Жёлтая' },
      { value: 'red', label: 'Красная' },
    ];
    const VEHICLE_COMPLETION_LABELS = {
      manually_entered: 'ручной ввод',
      partially_autofilled: 'частично автозаполнено',
      mostly_autofilled: 'почти заполнено',
      verified: 'проверено',
    };
    const VEHICLE_COMPLETION_OPTIONS = [
      { value: 'manually_entered', label: 'РУЧНОЙ ВВОД' },
      { value: 'partially_autofilled', label: 'ЧАСТИЧНО АВТО' },
      { value: 'mostly_autofilled', label: 'ПОЧТИ ЗАПОЛНЕНО' },
      { value: 'verified', label: 'ПРОВЕРЕНО' },
    ];
    const VEHICLE_FIELD_GROUPS = [
      {
        title: 'Идентификация',
        fields: [
          { name: 'make_display', label: 'Марка', placeholder: 'Audi' },
          { name: 'model_display', label: 'Модель', placeholder: 'A8 D4' },
          { name: 'production_year', label: 'Год', type: 'number', min: '1900', max: '2100', step: '1', placeholder: '2016' },
          { name: 'vin', label: 'VIN', placeholder: 'WAU...', copy: true, mono: true, wide: true, maxlength: '17' },
        ],
      },
      {
        title: 'Агрегаты',
        fields: [
          { name: 'engine_model', label: 'Модель двигателя', placeholder: '3.0 TFSI / K12B', wide: true },
          { name: 'gearbox_model', label: 'Модель КПП', placeholder: 'ZF 8HP55 / Aisin', wide: true },
          { name: 'drivetrain', label: 'Привод', placeholder: 'передний / задний / полный', wide: true },
        ],
      },
    ];
    VEHICLE_FIELD_GROUPS[0].title = '';
    VEHICLE_FIELD_GROUPS[0].fields.splice(
      3,
      0,
      { name: 'customer_phone', label: 'Телефон клиента', placeholder: '+7 900 123-45-67', wide: true },
      { name: 'customer_name', label: 'ФИО клиента', placeholder: 'Иван Иванов', wide: true },
    );
    const VEHICLE_FIELD_MAP = Object.fromEntries(
      VEHICLE_FIELD_GROUPS.flatMap((group) => group.fields.map((field) => [field.name, field]))
    );
    const VEHICLE_PRIMARY_FIELDS = VEHICLE_FIELD_GROUPS.flatMap((group) => group.fields.map((field) => field.name));
    const VEHICLE_META_FIELDS = [
      'manual_fields',
      'autofilled_fields',
      'tentative_fields',
      'field_sources',
      'raw_input_text',
      'raw_image_text',
      'image_parse_status',
      'warnings',
    ];

    const els = {
      boardScroll: document.querySelector('.board-scroll'),
      board: document.getElementById('board'),
      statusLine: document.getElementById('statusLine'),
      boardSettingsButton: document.getElementById('boardSettingsButton'),
      stickyDockButton: document.getElementById('stickyDockButton'),
      operatorButton: document.getElementById('operatorButton'),
      archiveButton: document.getElementById('archiveButton'),
      repairOrdersButton: document.getElementById('repairOrdersButton'),
      repairOrdersSearchInput: document.getElementById('repairOrdersSearchInput'),
      repairOrdersSortBy: document.getElementById('repairOrdersSortBy'),
      repairOrdersSortDir: document.getElementById('repairOrdersSortDir'),
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
      identityPassword: document.getElementById('identityPassword'),
      identityMeta: document.getElementById('identityMeta'),
      identitySave: document.getElementById('identitySave'),
      operatorProfileModal: document.getElementById('operatorProfileModal'),
      operatorProfileMeta: document.getElementById('operatorProfileMeta'),
      operatorSecurityNotice: document.getElementById('operatorSecurityNotice'),
      operatorStatsGrid: document.getElementById('operatorStatsGrid'),
      operatorActivityList: document.getElementById('operatorActivityList'),
      operatorAdminButton: document.getElementById('operatorAdminButton'),
      operatorLogoutButton: document.getElementById('operatorLogoutButton'),
      operatorAdminModal: document.getElementById('operatorAdminModal'),
      adminUsersList: document.getElementById('adminUsersList'),
      adminUserLogin: document.getElementById('adminUserLogin'),
      adminUserPassword: document.getElementById('adminUserPassword'),
      adminSaveUserButton: document.getElementById('adminSaveUserButton'),
      archiveModal: document.getElementById('archiveModal'),
      archiveList: document.getElementById('archiveList'),
      repairOrdersModal: document.getElementById('repairOrdersModal'),
      repairOrdersOpenTab: document.getElementById('repairOrdersOpenTab'),
      repairOrdersClosedTab: document.getElementById('repairOrdersClosedTab'),
      repairOrdersMeta: document.getElementById('repairOrdersMeta'),
      repairOrdersList: document.getElementById('repairOrdersList'),
      gptWallModal: document.getElementById('gptWallModal'),
      gptWallMeta: document.getElementById('gptWallMeta'),
      gptWallText: document.getElementById('gptWallText'),
      gptWallBoardTab: document.getElementById('gptWallBoardTab'),
      gptWallEventsTab: document.getElementById('gptWallEventsTab'),
      gptWallRefresh: document.getElementById('gptWallRefresh'),
""",
        r"""
      cardModal: document.getElementById('cardModal'),
      cardModalTitle: document.getElementById('cardModalTitle'),
      cardMetaLine: document.getElementById('cardMetaLine'),
      cardVehicle: document.getElementById('cardVehicle'),
      cardTitle: document.getElementById('cardTitle'),
      cardColumn: document.getElementById('cardColumn'),
      cardDescription: document.getElementById('cardDescription'),
      signalPreview: document.getElementById('signalPreview'),
      signalDays: document.getElementById('signalDaysStyled') || document.getElementById('signalDays'),
      signalHours: document.getElementById('signalHoursStyled') || document.getElementById('signalHours'),
      vehiclePanelSummary: document.getElementById('vehiclePanelSummary'),
      vehicleProfileFields: document.getElementById('vehicleProfileFields'),
      vehicleAutofillButton: document.getElementById('vehicleAutofillButton'),
      repairOrderButton: document.getElementById('repairOrderButton'),
      repairOrderModal: document.getElementById('repairOrderModal'),
      repairOrderModalTitle: document.getElementById('repairOrderModalTitle'),
      repairOrderNumber: document.getElementById('repairOrderNumber'),
      repairOrderDate: document.getElementById('repairOrderDate'),
      repairOrderOpenedAt: document.getElementById('repairOrderOpenedAt'),
      repairOrderClosedAt: document.getElementById('repairOrderClosedAt'),
      repairOrderStatus: document.getElementById('repairOrderStatus'),
      repairOrderClient: document.getElementById('repairOrderClient'),
      repairOrderPhone: document.getElementById('repairOrderPhone'),
      repairOrderVehicle: document.getElementById('repairOrderVehicle'),
      repairOrderLicensePlate: document.getElementById('repairOrderLicensePlate'),
      repairOrderVin: document.getElementById('repairOrderVin'),
      repairOrderMileage: document.getElementById('repairOrderMileage'),
      repairOrderReason: document.getElementById('repairOrderReason'),
      repairOrderComment: document.getElementById('repairOrderComment'),
      repairOrderNote: document.getElementById('repairOrderNote'),
      repairOrderTagMeta: document.getElementById('repairOrderTagMeta'),
      repairOrderTagColorPicker: document.getElementById('repairOrderTagColorPicker'),
      repairOrderTagList: document.getElementById('repairOrderTagList'),
      repairOrderTagInput: document.getElementById('repairOrderTagInput'),
      repairOrderTagAddButton: document.getElementById('repairOrderTagAddButton'),
      repairOrderWorksBody: document.getElementById('repairOrderWorksBody'),
      repairOrderMaterialsBody: document.getElementById('repairOrderMaterialsBody'),
      repairOrderAddWorkRowButton: document.getElementById('repairOrderAddWorkRowButton'),
      repairOrderAddMaterialRowButton: document.getElementById('repairOrderAddMaterialRowButton'),
      repairOrderAutofillButton: document.getElementById('repairOrderAutofillButton'),
      repairOrderCloseButton: document.getElementById('repairOrderCloseButton'),
      repairOrderSaveButton: document.getElementById('repairOrderSaveButton'),
      repairOrderPrintButton: document.getElementById('repairOrderPrintButton'),
      vehicleAutofillStatus: document.getElementById('vehicleAutofillStatus'),
      tagList: document.getElementById('tagList'),
      tagMeta: document.getElementById('tagMeta'),
      tagSuggestions: document.getElementById('tagSuggestions'),
      tagColorPicker: document.getElementById('tagColorPicker'),
      tagInput: document.getElementById('tagInput'),
      tagAddButton: document.getElementById('tagAddButton'),
      saveCardButton: document.getElementById('saveCardButton'),
      archiveAction: document.getElementById('archiveAction'),
      restoreAction: document.getElementById('restoreAction'),
      fileDropzone: document.getElementById('fileDropzone'),
      fileDropMeta: document.getElementById('fileDropMeta'),
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
      if (state.operatorSessionToken) request.headers['X-Operator-Session'] = state.operatorSessionToken;
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
      if (payload?.error?.details?.auth_type === 'operator_session') {
        if (response.status === 401) {
          clearOperatorSession({ openLogin: true, preserveStatus: true });
        }
        throw new Error(payload?.error?.message || 'Нужен вход оператора.');
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

    function withObjectUrl(blob, callback, { revokeDelay = 1500 } = {}) {
      const objectUrl = URL.createObjectURL(blob);
      try {
        callback(objectUrl);
      } finally {
        setTimeout(() => URL.revokeObjectURL(objectUrl), revokeDelay);
      }
    }

    function triggerBlobDownload(blob, fileName) {
      withObjectUrl(blob, (objectUrl) => {
        const link = document.createElement('a');
        link.href = objectUrl;
        link.download = String(fileName || 'attachment.bin');
        document.body.appendChild(link);
        link.click();
        link.remove();
      });
    }

    async function downloadAttachment(url) {
      const headers = {};
      if (state.apiToken) headers.Authorization = 'Bearer ' + state.apiToken;
      if (state.operatorSessionToken) headers['X-Operator-Session'] = state.operatorSessionToken;
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
      triggerBlobDownload(blob, extractDownloadName(response, 'attachment.bin'));
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

    function configureOperatorIdentityUi() {
      const title = els.identityModal?.querySelector('.dialog__title');
      if (title) title.textContent = 'ВХОД ОПЕРАТОРА';
      const loginLabel = document.querySelector('label[for="identityInput"]');
      if (loginLabel) loginLabel.textContent = 'ЛОГИН';
      if (els.identityInput) {
        els.identityInput.placeholder = 'ADMIN';
        els.identityInput.autocomplete = 'username';
      }
      if (!els.identityPassword && els.identityInput?.parentElement) {
        els.identityInput.parentElement.insertAdjacentHTML(
          'afterend',
          '<div class="field"><label for="identityPassword">ПАРОЛЬ</label><input id="identityPassword" type="password" maxlength="120" placeholder="••••••••" autocomplete="current-password"></div>'
        );
        els.identityPassword = document.getElementById('identityPassword');
      }
      if (!els.identityMeta) {
        const existingMeta = els.identityModal?.querySelector('.dialog__foot .log-row__meta');
        if (existingMeta) {
          existingMeta.id = 'identityMeta';
          els.identityMeta = existingMeta;
        }
      }
      if (els.identityMeta) {
        els.identityMeta.textContent = 'Логин определяет профиль и статистику действий на доске.';
      } else if (els.identitySave?.parentElement) {
        els.identitySave.insertAdjacentHTML(
          'beforebegin',
          '<div class="log-row__meta" id="identityMeta">Логин определяет профиль и статистику действий на доске.</div>'
        );
        els.identityMeta = document.getElementById('identityMeta');
      }
      if (els.identitySave) els.identitySave.textContent = 'ВОЙТИ';
    }

    function setOperatorSessionToken(token, { persist = true } = {}) {
      const normalized = String(token || '').trim();
      state.operatorSessionToken = normalized;
      if (persist) {
        if (normalized) localStorage.setItem(OPERATOR_SESSION_STORAGE_KEY, normalized);
        else localStorage.removeItem(OPERATOR_SESSION_STORAGE_KEY);
      }
      return normalized;
    }

    function closeOperatorLoginModal() {
      els.identityModal.classList.remove('is-open');
    }

    function openOperatorLoginModal() {
      els.identityInput.value = '';
      if (els.identityPassword) els.identityPassword.value = '';
      els.identityModal.classList.add('is-open');
      requestAnimationFrame(() => els.identityInput.focus());
    }

    function updateOperatorButton() {
      if (!state.actor) {
        els.operatorButton.textContent = 'ОПЕРАТОР';
        return;
      }
      const rolePrefix = state.operatorProfile?.user?.is_admin ? 'АДМИН' : 'ОПЕРАТОР';
      els.operatorButton.textContent = rolePrefix + ': ' + state.actor;
    }

    function clearOperatorSession({ openLogin = false, preserveStatus = false } = {}) {
      state.actor = '';
      state.operatorProfile = null;
      state.operatorUsers = [];
      setOperatorSessionToken('');
      updateOperatorButton();
      els.operatorProfileModal.classList.remove('is-open');
      els.operatorAdminModal.classList.remove('is-open');
      els.operatorSecurityNotice.classList.add('hidden');
      els.operatorSecurityNotice.textContent = '';
      if (!preserveStatus) setStatus('Нужен вход оператора.', true);
      if (openLogin) openOperatorLoginModal();
    }

    function requireOperatorSession() {
      if (state.operatorSessionToken && state.actor) return true;
      openOperatorLoginModal();
      setStatus('Нужен вход оператора.', true);
      return false;
    }

    function operatorStatHtml(label, value) {
      return '<div class="operator-stat"><div class="operator-stat__label">' + escapeHtml(label) + '</div><div class="operator-stat__value">' + escapeHtml(value) + '</div></div>';
    }

    function renderOperatorActivity(actions) {
      els.operatorActivityList.innerHTML = actions.length
        ? actions.map((item) => '<div class="log-row">' + escapeHtml([formatDate(item.timestamp), item.message].join(' | ')) + '</div>').join('')
        : '<div class="log-row__meta">ДЕЙСТВИЙ ПОКА НЕТ.</div>';
    }

    function renderOperatorProfile(data, { openModal = false } = {}) {
      state.operatorProfile = data;
      state.actor = data?.user?.username || '';
      setOperatorSessionToken(data?.session?.token || state.operatorSessionToken);
      updateOperatorButton();
      const stats = data?.stats || {};
      els.operatorProfileMeta.textContent =
        'ПОЛЬЗОВАТЕЛЬ: ' + (data?.user?.username || '-') +
        ' | РОЛЬ: ' + (data?.user?.is_admin ? 'АДМИНИСТРАТОР' : 'ОПЕРАТОР') +
        ' | ОБНОВЛЕНО: ' + formatDate(data?.user?.updated_at);
      const securityWarning = data?.security?.warning || '';
      els.operatorSecurityNotice.classList.toggle('hidden', !securityWarning);
      els.operatorSecurityNotice.textContent = securityWarning;
      els.operatorStatsGrid.innerHTML = [
        operatorStatHtml('ОТКРЫТО КАРТОЧЕК', stats.cards_opened ?? 0),
        operatorStatHtml('СОЗДАНО', stats.cards_created ?? 0),
        operatorStatHtml('ЗАКРЫТО', stats.cards_archived ?? 0),
        operatorStatHtml('ПЕРЕМЕЩЕНИЙ', stats.card_moves ?? 0),
      ].join('');
      renderOperatorActivity(data?.recent_actions || []);
      els.operatorAdminButton.classList.toggle('hidden', !data?.user?.is_admin);
      closeOperatorLoginModal();
      if (openModal) els.operatorProfileModal.classList.add('is-open');
    }

    async function loadOperatorProfile(openModal = false) {
      const data = await api('/api/get_operator_profile');
      renderOperatorProfile(data, { openModal });
      return data;
    }

    async function openOperatorWorkspace() {
      if (!state.operatorSessionToken) {
        openOperatorLoginModal();
        return;
      }
      try {
        await loadOperatorProfile(true);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function loginOperator() {
      try {
        const data = await api('/api/login_operator', {
          method: 'POST',
          body: {
            username: els.identityInput.value,
            password: els.identityPassword ? els.identityPassword.value : '',
          },
        });
        renderOperatorProfile(data, { openModal: true });
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    function handleIdentityInputKeydown(event) {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      if (els.identityPassword) els.identityPassword.focus();
      else loginOperator();
    }

    function handleIdentityPasswordKeydown(event) {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      loginOperator();
    }

    async function logoutOperator() {
      try {
        if (state.operatorSessionToken) await api('/api/logout_operator', { method: 'POST', body: {} });
      } catch (_) {
      } finally {
        clearOperatorSession({ openLogin: true, preserveStatus: true });
      }
    }

    function renderOperatorUsers(data) {
      const users = data?.users || [];
      state.operatorUsers = users;
      els.adminUsersList.innerHTML = users.length
        ? users.map((user) => {
            const stats = user.stats || {};
            return '<div class="operator-user-row">' +
              '<div class="operator-user-row__head"><strong>' + escapeHtml(user.username) + '</strong><span class="operator-user-chip">' + escapeHtml(user.is_admin ? 'АДМИН' : 'ОПЕРАТОР') + '</span></div>' +
              '<div class="operator-user-row__stats">' +
                '<span class="operator-user-chip">ОТКРЫТО: ' + escapeHtml(stats.cards_opened ?? 0) + '</span>' +
                '<span class="operator-user-chip">ЗАКРЫТО: ' + escapeHtml(stats.cards_archived ?? 0) + '</span>' +
                '<span class="operator-user-chip">ПЕРЕМЕЩЕНИЙ: ' + escapeHtml(stats.card_moves ?? 0) + '</span>' +
              '</div>' +
              '<div class="operator-user-row__actions"><span class="log-row__meta">ОБНОВЛЕНО: ' + escapeHtml(formatDate(user.updated_at)) + ' | СТАТИСТИКА: 15 ДНЕЙ</span><div style="display:flex; gap:8px; flex-wrap:wrap;"><button class="btn" type="button" data-open-operator-report="' + escapeHtml(user.username) + '">СТАТИСТИКА</button><button class="btn btn--danger" type="button" data-delete-operator-user="' + escapeHtml(user.username) + '">УДАЛИТЬ</button></div></div>' +
            '</div>';
          }).join('')
        : '<div class="log-row__meta">ПОЛЬЗОВАТЕЛЕЙ ПОКА НЕТ.</div>';
    }

    function openTextBlobWindow(text, fileName) {
      const blob = new Blob([String(text || '').trim() + '\\n'], { type: 'text/plain;charset=utf-8' });
      withObjectUrl(blob, (objectUrl) => {
        const opened = window.open(objectUrl, '_blank', 'noopener');
        if (!opened) {
          const link = document.createElement('a');
          link.href = objectUrl;
          link.download = String(fileName || 'report.txt');
          document.body.appendChild(link);
          link.click();
          link.remove();
        }
      }, { revokeDelay: 60_000 });
    }

    function maybeOpenModal(modalEl, openModal) {
      if (openModal && modalEl) modalEl.classList.add('is-open');
    }

    function openArchiveModal() {
      renderArchive();
      maybeOpenModal(els.archiveModal, true);
    }

    function closeNamedModal(closeKey) {
      const closeActions = {
        card: () => closeCardModal(),
        archive: () => els.archiveModal.classList.remove('is-open'),
        'repair-orders': () => els.repairOrdersModal.classList.remove('is-open'),
        wall: () => els.gptWallModal.classList.remove('is-open'),
        settings: () => els.boardSettingsModal.classList.remove('is-open'),
        sticky: () => closeStickyModal(),
        'repair-order': () => closeRepairOrderModal(),
        'operator-profile': () => els.operatorProfileModal.classList.remove('is-open'),
        'operator-admin': () => els.operatorAdminModal.classList.remove('is-open'),
      };
      const closeAction = closeActions[String(closeKey || '')];
      if (typeof closeAction === 'function') closeAction();
    }

    async function loadModalData(path, { method = 'GET', body = null, openModal = false, modalEl = null, onSuccess, onError } = {}) {
      try {
        const request = { method };
        if (body !== null) request.body = body;
        const data = await api(path, request);
        if (typeof onSuccess === 'function') onSuccess(data);
        maybeOpenModal(modalEl, openModal);
        return data;
      } catch (error) {
        if (typeof onError === 'function') onError(error);
        maybeOpenModal(modalEl, openModal);
        setStatus(error.message, true);
        return null;
      }
    }

    async function reloadOperatorAdminUsers({ openModal = false } = {}) {
      return loadModalData('/api/list_operator_users', {
        openModal,
        modalEl: els.operatorAdminModal,
        onSuccess: renderOperatorUsers,
      });
    }

    async function refreshOperatorAdminSurfaces({ openAdminModal = false, refreshProfile = false } = {}) {
      const tasks = [reloadOperatorAdminUsers({ openModal: openAdminModal })];
      if (refreshProfile) tasks.push(loadOperatorProfile(false));
      await Promise.all(tasks);
    }

    async function openOperatorUserReport(username) {
      try {
        const data = await api('/api/get_operator_user_report?username=' + encodeURIComponent(username));
        const text = String(data?.text || '').trim();
        if (!text) {
          setStatus('ОТЧЁТ ПУСТ.', true);
          return;
        }
        openTextBlobWindow(text, data?.file_name || ('operator-report-' + username + '.txt'));
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    function handleAdminUsersListClick(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const reportUser = target.dataset.openOperatorReport;
      if (reportUser) {
        openOperatorUserReport(reportUser);
        return;
      }
      const username = target.dataset.deleteOperatorUser;
      if (username) deleteOperatorUser(username);
    }

    async function openOperatorAdminModal() {
      await reloadOperatorAdminUsers({ openModal: true });
    }

    async function saveOperatorUser() {
      try {
        const data = await api('/api/save_operator_user', {
          method: 'POST',
          body: {
            username: els.adminUserLogin.value,
            password: els.adminUserPassword.value,
          },
        });
        els.adminUserLogin.value = '';
        els.adminUserPassword.value = '';
        setStatus((data?.meta?.created ? 'Пользователь создан.' : 'Пользователь обновлён.') + ' ' + (data?.user?.username || ''), false);
        await refreshOperatorAdminSurfaces({ openAdminModal: true, refreshProfile: true });
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function deleteOperatorUser(username) {
      if (!window.confirm('Удалить пользователя ' + username + '?')) return;
      try {
        await api('/api/delete_operator_user', { method: 'POST', body: { username } });
        await refreshOperatorAdminSurfaces({ openAdminModal: true });
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function bootstrapOperatorSession() {
      updateOperatorButton();
      if (!state.operatorSessionToken) {
        openOperatorLoginModal();
        return;
      }
      try {
        await loadOperatorProfile(false);
      } catch (_) {
        clearOperatorSession({ openLogin: true, preserveStatus: true });
      }
    }

    /* Legacy pre-session operator helper removed.
      return requireOperatorSession();
    */

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

    function limitCardModalHeading(value, maxLength = 92) {
      const text = String(value || '').trim();
      if (!text) return 'Рабочая карточка';
      if (text.length <= maxLength) return text;
      return text.slice(0, Math.max(0, maxLength - 1)).trimEnd() + '…';
    }

    function configureVehicleAutofillUi() {
      return;
    }

    function syncCardDescriptionHeight() {
      const textarea = els.cardDescription;
      if (!textarea) return;
      const style = window.getComputedStyle(textarea);
      const lineHeight = Math.max(22, parseFloat(style.lineHeight || '24'));
      const paddingTop = parseFloat(style.paddingTop || '0');
      const paddingBottom = parseFloat(style.paddingBottom || '0');
      const borderTop = parseFloat(style.borderTopWidth || '0');
      const borderBottom = parseFloat(style.borderBottomWidth || '0');
      const chromeHeight = paddingTop + paddingBottom + borderTop + borderBottom;
      const text = String(textarea.value || '').trim();
      const lineCount = text ? text.split(/\r?\n/).length : 0;
      const minRows = text ? Math.max(6, Math.min(10, lineCount + 1)) : 6;
      const minHeight = Math.round(minRows * lineHeight + chromeHeight);
      const maxHeight = Math.max(minHeight, Math.min(window.innerHeight * 0.56, 720));
      textarea.style.height = 'auto';
      textarea.style.height = Math.max(minHeight, Math.min(textarea.scrollHeight, maxHeight)) + 'px';
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

    function normalizeTagColor(color) {
      const value = String(color || '').trim().toLowerCase();
      return TAG_COLOR_OPTIONS.some((item) => item.value === value) ? value : 'green';
    }

    function normalizeDraftTag(raw, fallbackColor = 'green') {
      if (raw === null || raw === undefined) return null;
      if (typeof raw === 'string') {
        const label = raw.trim().toUpperCase();
        return label ? { label, color: normalizeTagColor(fallbackColor) } : null;
      }
      const label = String(raw.label || '').trim().toUpperCase();
      if (!label) return null;
      return { label, color: normalizeTagColor(raw.color || fallbackColor) };
    }

    function normalizeUiTags(items, fallbackColor = 'green', limit = CARD_TAG_LIMIT) {
      if (!Array.isArray(items)) return [];
      const tagsByLabel = new Map();
      items.forEach((item) => {
        const normalized = normalizeDraftTag(item, fallbackColor);
        if (!normalized) return;
        tagsByLabel.set(normalized.label, normalized);
      });
      return Array.from(tagsByLabel.values()).slice(0, Math.max(1, Number(limit) || 1));
    }

    function normalizeDraftTags(items, fallbackColor = 'green') {
      return normalizeUiTags(items, fallbackColor, CARD_TAG_LIMIT);
    }

    function normalizeRepairOrderTags(items, fallbackColor = 'green') {
      return normalizeUiTags(items, fallbackColor, REPAIR_ORDER_TAG_LIMIT);
    }

    function upsertDraftTag(tag) {
      const normalized = normalizeDraftTag(tag, state.draftTagColor);
      if (!normalized) return false;
      const exists = state.draftTags.some((item) => item.label === normalized.label);
      if (!exists && state.draftTags.length >= CARD_TAG_LIMIT) {
        setStatus('НА КАРТОЧКЕ МОЖЕТ БЫТЬ НЕ БОЛЕЕ 3 МЕТОК.', true);
        return false;
      }
      const nextTags = normalizeDraftTags(state.draftTags.concat([normalized]), state.draftTagColor);
      state.draftTags = nextTags;
      return true;
    }

    function renderTagColorPicker() {
      els.tagColorPicker.innerHTML = TAG_COLOR_OPTIONS.map((option) => {
        const activeClass = option.value === state.draftTagColor ? ' is-active' : '';
        return '<button class="tag-color-option' + activeClass + '" type="button" data-tag-color-choice="' + option.value + '" data-tag-color="' + option.value + '" title="' + option.label + '" aria-label="' + option.label + '"></button>';
      }).join('');
    }

    function addDraftTag() {
      const tag = normalizeDraftTag({ label: els.tagInput.value, color: state.draftTagColor }, state.draftTagColor);
      if (!tag) return;
      if (!upsertDraftTag(tag)) return;
      els.tagInput.value = '';
      renderColorTags();
    }

    function handleTagInputKeydown(event) {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      addDraftTag();
    }

    function addSuggestedTag(tag) {
      const normalized = normalizeDraftTag(tag, state.draftTagColor);
      if (!normalized) return;
      if (!upsertDraftTag(normalized)) return;
      renderColorTags();
    }

    function upsertRepairOrderTag(tag) {
      const normalized = normalizeDraftTag(tag, state.repairOrderTagColor);
      if (!normalized) return false;
      const exists = state.repairOrderTags.some((item) => item.label === normalized.label);
      if (!exists && state.repairOrderTags.length >= REPAIR_ORDER_TAG_LIMIT) {
        setStatus('НА ЗАКАЗ-НАРЯДЕ МОЖЕТ БЫТЬ НЕ БОЛЕЕ 5 МЕТОК.', true);
        return false;
      }
      state.repairOrderTags = normalizeRepairOrderTags(state.repairOrderTags.concat([normalized]), state.repairOrderTagColor);
      return true;
    }

    function renderRepairOrderTagColorPicker() {
      if (!els.repairOrderTagColorPicker) return;
      els.repairOrderTagColorPicker.innerHTML = TAG_COLOR_OPTIONS.map((option) => {
        const activeClass = option.value === state.repairOrderTagColor ? ' is-active' : '';
        return '<button class="tag-color-option' + activeClass + '" type="button" data-repair-order-tag-color-choice="' + option.value + '" data-tag-color="' + option.value + '" title="' + option.label + '" aria-label="' + option.label + '"></button>';
      }).join('');
    }

    function renderRepairOrderTags() {
      if (!els.repairOrderTagList || !els.repairOrderTagInput || !els.repairOrderTagAddButton) return;
      const atLimit = state.repairOrderTags.length >= REPAIR_ORDER_TAG_LIMIT;
      renderRepairOrderTagColorPicker();
      els.repairOrderTagList.innerHTML = state.repairOrderTags.length
        ? state.repairOrderTags.map((tag) => (
            '<span class="repair-order-tag-item">'
            + '<button class="tag repair-order-tag-edit" type="button" data-tag-color="' + escapeHtml(tag.color) + '" data-edit-repair-order-tag="' + escapeHtml(tag.label) + '" title="Редактировать метку"><span class="tag__dot"></span>' + escapeHtml(tag.label) + '</button>'
            + '<button class="btn btn--ghost repair-order-tag-remove" type="button" data-remove-repair-order-tag="' + escapeHtml(tag.label) + '" aria-label="Удалить метку">×</button>'
            + '</span>'
          )).join('')
        : '<div class="tag tag--muted">МЕТОК НЕТ</div>';
      if (els.repairOrderTagMeta) {
        els.repairOrderTagMeta.textContent = state.repairOrderTags.length + ' / ' + REPAIR_ORDER_TAG_LIMIT;
        els.repairOrderTagMeta.dataset.limitState = atLimit ? 'full' : 'open';
      }
      els.repairOrderTagInput.disabled = atLimit;
      els.repairOrderTagInput.placeholder = atLimit ? 'ЛИМИТ 5 / 5' : 'МЕТКА';
      els.repairOrderTagAddButton.disabled = atLimit;
    }

    function addRepairOrderTag() {
      const tag = normalizeDraftTag({ label: els.repairOrderTagInput?.value, color: state.repairOrderTagColor }, state.repairOrderTagColor);
      if (!tag) return;
      if (!upsertRepairOrderTag(tag)) return;
      els.repairOrderTagInput.value = '';
      renderRepairOrderTags();
    }

    function editRepairOrderTag(label) {
      const tagLabel = String(label || '').trim();
      if (!tagLabel) return;
      const tag = state.repairOrderTags.find((item) => item.label === tagLabel);
      if (!tag) return;
      state.repairOrderTagColor = normalizeTagColor(tag.color);
      state.repairOrderTags = state.repairOrderTags.filter((item) => item.label !== tagLabel);
      if (els.repairOrderTagInput) {
        els.repairOrderTagInput.value = tag.label;
        els.repairOrderTagInput.focus();
      }
      renderRepairOrderTags();
    }

    function removeRepairOrderTag(label) {
      state.repairOrderTags = state.repairOrderTags.filter((tag) => tag.label !== String(label || '').trim());
      renderRepairOrderTags();
    }

    function handleRepairOrderTagInputKeydown(event) {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      addRepairOrderTag();
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

    function clipboardTextAttachmentName() {
      const stamp = new Date().toISOString().replace(/\.\d+Z$/, 'Z').replace(/[:T]/g, '-');
      return 'clipboard-' + stamp + '.txt';
    }

    function collectClipboardAttachmentFiles(event) {
      const files = [];
      const items = Array.from(event.clipboardData?.items || []);
      items.forEach((item) => {
        if (item.kind !== 'file') return;
        const file = item.getAsFile();
        if (file) files.push(file);
      });
      if (files.length) return files;
      const text = event.clipboardData?.getData('text/plain') || '';
      if (!text.trim()) return [];
      return [new File([text], clipboardTextAttachmentName(), { type: 'text/plain;charset=utf-8' })];
    }

    function vehicleInputId(fieldName) {
      return 'vehicleField_' + fieldName;
    }

    function emptyVehicleProfile() {
      const profile = {
        manual_fields: [],
        autofilled_fields: [],
        tentative_fields: [],
        field_sources: {},
        raw_input_text: '',
        raw_image_text: '',
        image_parse_status: 'not_attempted',
        warnings: [],
      };
      VEHICLE_PRIMARY_FIELDS.forEach((fieldName) => {
        profile[fieldName] = VEHICLE_FIELD_MAP[fieldName]?.type === 'number' ? null : '';
      });
      return profile;
    }

    function cloneVehicleProfile(profile) {
      const cloned = emptyVehicleProfile();
      if (profile && typeof profile === 'object') {
        const raw = JSON.parse(JSON.stringify(profile));
        Object.assign(cloned, raw);
      }
      cloned.source_links_or_refs = Array.isArray(cloned.source_links_or_refs) ? cloned.source_links_or_refs : [];
      cloned.manual_fields = Array.isArray(cloned.manual_fields) ? Array.from(new Set(cloned.manual_fields)) : [];
      cloned.autofilled_fields = Array.isArray(cloned.autofilled_fields) ? Array.from(new Set(cloned.autofilled_fields)) : [];
      cloned.tentative_fields = Array.isArray(cloned.tentative_fields) ? Array.from(new Set(cloned.tentative_fields)) : [];
      cloned.field_sources = cloned.field_sources && typeof cloned.field_sources === 'object' ? cloned.field_sources : {};
      cloned.warnings = Array.isArray(cloned.warnings) ? cloned.warnings : [];
      return cloned;
    }

    function normalizeVehicleNumber(rawValue, { integer = false } = {}) {
      const value = String(rawValue ?? '').trim().replace(',', '.');
      if (!value) return null;
      const parsed = integer ? Number.parseInt(value, 10) : Number.parseFloat(value);
      return Number.isFinite(parsed) ? parsed : null;
    }

    function normalizeVehicleLinksInput(rawValue) {
      if (Array.isArray(rawValue)) return rawValue.map((item) => String(item || '').trim()).filter(Boolean);
      return String(rawValue || '')
        .split(/[\n,]/)
        .map((item) => item.trim())
        .filter(Boolean);
    }

    function vehicleDisplayFromProfile(profile) {
      const parts = [profile?.make_display, profile?.model_display].filter(Boolean);
      if (!parts.length) return '';
      const base = parts.join(' ');
      return profile?.production_year ? (base + ' ' + profile.production_year) : base;
    }

    function vehicleCompletionLabel(value) {
      return VEHICLE_COMPLETION_LABELS[String(value || '').trim()] || 'данные уточняются';
    }

    function vinLooksSuspicious(value) {
      const normalized = String(value || '').toUpperCase().replace(/\s+/g, '');
      if (!normalized) return false;
      if (normalized.length !== 17) return true;
      if (/[IOQ]/.test(normalized)) return true;
      return !/^[A-HJ-NPR-Z0-9]{17}$/.test(normalized);
    }

    function vehicleFieldControlHtml(field) {
      const inputId = vehicleInputId(field.name);
      if (field.kind === 'textarea') {
        return '<textarea id="' + inputId + '" data-vehicle-field-input="' + field.name + '" placeholder="' + escapeHtml(field.placeholder || '') + '"></textarea>';
      }
      if (field.kind === 'select') {
        return '<select id="' + inputId + '" data-vehicle-field-input="' + field.name + '">' +
          (field.options || []).map((option) => '<option value="' + escapeHtml(option.value) + '">' + escapeHtml(option.label) + '</option>').join('') +
          '</select>';
      }
      const attrs = [
        'id="' + inputId + '"',
        'data-vehicle-field-input="' + field.name + '"',
        'type="' + escapeHtml(field.type || 'text') + '"',
        field.placeholder ? 'placeholder="' + escapeHtml(field.placeholder) + '"' : '',
        field.min ? 'min="' + escapeHtml(field.min) + '"' : '',
        field.max ? 'max="' + escapeHtml(field.max) + '"' : '',
        field.step ? 'step="' + escapeHtml(field.step) + '"' : '',
        field.maxlength ? 'maxlength="' + escapeHtml(field.maxlength) + '"' : '',
        field.mono ? 'class="vehicle-control--mono"' : '',
      ].filter(Boolean).join(' ');
      return '<input ' + attrs + '>';
    }

    function renderVehicleProfileFields() {
      els.vehicleProfileFields.innerHTML = VEHICLE_FIELD_GROUPS.map((group) => {
        const fields = group.fields.map((field) => {
          const copyButton = field.copy
            ? '<button class="vehicle-copy" type="button" data-copy-vehicle-field="' + escapeHtml(field.name) + '">копия</button>'
            : '';
          return '<div class="field field--compact vehicle-field' + (field.wide ? ' vehicle-field--wide' : '') + '">' +
            '<div class="vehicle-field__label"><span>' + escapeHtml(field.label) + '</span>' + copyButton + '</div>' +
            vehicleFieldControlHtml(field) +
            '</div>';
        }).join('');
        return '<section class="vehicle-group">' +
          (group.title ? '<div class="vehicle-group__title">' + escapeHtml(group.title) + '</div>' : '') +
          '<div class="vehicle-group__grid">' + fields + '</div>' +
          '</section>';
      }).join('');
      els.vehicleProfileFields.querySelectorAll('[data-vehicle-field-input]').forEach((input) => {
        input.addEventListener('input', () => handleVehicleFieldInput(input.dataset.vehicleFieldInput));
        input.addEventListener('change', () => handleVehicleFieldInput(input.dataset.vehicleFieldInput));
      });
    }

    function getVehicleFieldInput(fieldName) {
      return document.getElementById(vehicleInputId(fieldName));
    }

    function setVehicleFieldValue(fieldName, value) {
      const input = getVehicleFieldInput(fieldName);
      if (!input) return;
      if (Array.isArray(value)) {
        input.value = value.join('\\n');
        return;
      }
      input.value = value === null || value === undefined ? '' : String(value);
    }

    function readVehicleFieldValue(fieldName) {
      const input = getVehicleFieldInput(fieldName);
      const field = VEHICLE_FIELD_MAP[fieldName] || {};
      if (!input) return field.type === 'number' ? null : '';
      if (field.kind === 'textarea' && fieldName === 'source_links_or_refs') return normalizeVehicleLinksInput(input.value);
      if (field.kind === 'select') return String(input.value || '').trim();
      if (field.type === 'number') return normalizeVehicleNumber(input.value, { integer: field.step === '1' });
      return String(input.value || '').trim();
    }

    function defaultVehicleStatusText(profile) {
      const lines = [];
      if (profile?.source_summary) lines.push('Источник: ' + profile.source_summary);
      if (profile?.source_confidence !== null && profile?.source_confidence !== undefined && profile.source_confidence !== '') {
        lines.push('Confidence: ' + Math.round(Number(profile.source_confidence) * 100) + '%');
      }
      if (profile?.image_parse_status && profile.image_parse_status !== 'not_attempted') {
        lines.push('OCR: ' + profile.image_parse_status);
      }
      if (Array.isArray(profile?.source_links_or_refs) && profile.source_links_or_refs.length) {
        lines.push('Refs: ' + profile.source_links_or_refs.join(' | '));
      }
      (profile?.warnings || []).forEach((warning) => lines.push('! ' + warning));
      return lines.length ? lines.join('\\n') : 'Автозаполнение пока не запускалось.';
    }

    function renderVehicleAutofillStatus(message, isWarning = false) {
      if (!els.vehicleAutofillStatus) return;
      const text = String(message || '').trim() || 'Автозаполнение пока не запускалось.';
      els.vehicleAutofillStatus.textContent = text;
      els.vehicleAutofillStatus.classList.toggle('is-warning', isWarning);
      els.vehicleAutofillStatus.classList.toggle('is-empty', !text || text === 'Автозаполнение пока не запускалось.');
    }

    cardUnreadBadgeHtml = function(card) {
      return card?.is_unread
        ? '<div class="card__unread-badge" title="Не прочитано" aria-label="Не прочитано">NEW</div>'
        : '';
    };

    cardHtml = function(card) {
      const previewTags = (card.tags || []).slice(0, CARD_TAG_LIMIT);
      const extraTags = (card.tags || []).length - previewTags.length;
      const tagsHtml = previewTags.length
        ? previewTags.map((tag) => '<span class="tag">' + escapeHtml(tag) + '</span>').join('') + (extraTags > 0 ? '<span class="tag">+' + extraTags + '</span>' : '')
        : '<span class="tag tag--muted">БЕЗ МЕТОК</span>';
      const headingHtml = buildCardHeadingHtml(card);
      return '<article class="card" draggable="true" data-card-id="' + escapeHtml(card.id) + '" data-indicator="' + escapeHtml(card.indicator) + '" data-status="' + escapeHtml(card.status) + '" data-blink="' + (card.is_blinking ? 'true' : 'false') + '">' + headingHtml + '<div class="card__desc">' + escapeHtml(card.description || 'Описание не указано') + '</div><div class="card__signal"><span class="card__signal-label"><span class="lamp" data-indicator="' + escapeHtml(card.indicator) + '"></span><span>СИГН</span></span><span class="card__signal-value">' + durationToMarkup(card.remaining_seconds, false) + '</span></div><div class="card__tags">' + tagsHtml + '</div><div class="meta-line"><span>ФАЙЛЫ ' + escapeHtml(card.attachment_count) + '</span><span>ЖУРНАЛ ' + escapeHtml(card.events_count) + '</span></div></article>';
    };

    renderCardHtml = function(card) {
      const normalizedTags = normalizeDraftTags(card.tag_items || card.tags || []);
      const previewTags = normalizedTags.slice(0, CARD_TAG_LIMIT);
      const extraTags = normalizedTags.length - previewTags.length;
      const tagsHtml = previewTags.length
        ? previewTags.map((tag) => '<span class="tag" data-tag-color="' + escapeHtml(tag.color) + '"><span class="tag__dot"></span>' + escapeHtml(tag.label) + '</span>').join('') + (extraTags > 0 ? '<span class="tag">+' + extraTags + '</span>' : '')
        : '<span class="tag tag--muted">БЕЗ МЕТОК</span>';
      const headingHtml = buildCardHeadingHtml(card);
      const heatStyle = '--deadline-heat-border:' + escapeHtml(card.deadline_heat_border_color || 'rgba(83, 191, 122, 0.34)') + ';--deadline-heat-ring:' + escapeHtml(card.deadline_heat_ring_color || 'rgba(83, 191, 122, 0.08)') + ';--deadline-heat-glow:' + escapeHtml(card.deadline_heat_glow_color || 'rgba(83, 191, 122, 0.04)') + ';';
      return '<article class="card" style="' + heatStyle + '" draggable="true" data-card-id="' + escapeHtml(card.id) + '" data-indicator="' + escapeHtml(card.indicator) + '" data-status="' + escapeHtml(card.status) + '" data-blink="' + (card.is_blinking ? "true" : "false") + '" data-deadline-bucket="' + escapeHtml(card.deadline_progress_bucket ?? 0) + '" data-deadline-step="' + escapeHtml(card.deadline_progress_step_percent ?? 0) + '">' + headingHtml + '<div class="card__desc">' + escapeHtml(card.description || 'Описание не указано') + '</div><div class="card__signal"><span class="card__signal-label"><span class="lamp" data-indicator="' + escapeHtml(card.indicator) + '"></span><span>СИГН</span></span><span class="card__signal-value">' + durationToMarkup(card.remaining_seconds, false) + '</span></div><div class="card__tags">' + tagsHtml + '</div><div class="meta-line"><span>ФАЙЛЫ ' + escapeHtml(card.attachment_count) + '</span><span>ЖУРНАЛ ' + escapeHtml(card.events_count) + '</span></div></article>';
    };

    renderBoardCardHtml = function(card) {
      const normalizedTags = normalizeDraftTags(card.tag_items || card.tags || []);
      const previewTags = normalizedTags.slice(0, CARD_TAG_LIMIT);
      const extraTags = normalizedTags.length - previewTags.length;
      const tagsHtml = previewTags.length
        ? previewTags.map((tag) => '<span class="tag" data-tag-color="' + escapeHtml(tag.color) + '"><span class="tag__dot"></span>' + escapeHtml(tag.label) + '</span>').join('') + (extraTags > 0 ? '<span class="tag">+' + extraTags + '</span>' : '')
        : '<span class="tag tag--muted">БЕЗ МЕТОК</span>';
      const headingHtml = buildCardHeadingHtml(card);
      const unreadBadgeHtml = cardUnreadBadgeHtml(card);
      const heatStyle = '--deadline-heat-border:' + escapeHtml(card.deadline_heat_border_color || 'rgba(83, 191, 122, 0.34)') + ';--deadline-heat-ring:' + escapeHtml(card.deadline_heat_ring_color || 'rgba(83, 191, 122, 0.08)') + ';--deadline-heat-glow:' + escapeHtml(card.deadline_heat_glow_color || 'rgba(83, 191, 122, 0.04)') + ';';
      return '<article class="card" style="' + heatStyle + '" draggable="true" data-card-id="' + escapeHtml(card.id) + '" data-indicator="' + escapeHtml(card.indicator) + '" data-status="' + escapeHtml(card.status) + '" data-blink="' + (card.is_blinking ? "true" : "false") + '" data-unread="' + (card.is_unread ? 'true' : 'false') + '" data-deadline-bucket="' + escapeHtml(card.deadline_progress_bucket ?? 0) + '" data-deadline-step="' + escapeHtml(card.deadline_progress_step_percent ?? 0) + '">' + unreadBadgeHtml + headingHtml + '<div class="card__desc">' + escapeHtml(card.description || 'Описание не указано') + '</div><div class="card__signal"><span class="card__signal-label"><span class="lamp" data-indicator="' + escapeHtml(card.indicator) + '"></span><span>СИГН</span></span><span class="card__signal-value">' + durationToMarkup(card.remaining_seconds, false) + '</span></div><div class="card__tags">' + tagsHtml + '</div><div class="meta-line"><span>ФАЙЛЫ ' + escapeHtml(card.attachment_count) + '</span><span>ЖУРНАЛ ' + escapeHtml(card.events_count) + '</span></div></article>';
    };

    function legacyRefreshVehiclePanelShadow() {
      const profile = cloneVehicleProfile(state.vehicleProfileDraft || emptyVehicleProfile());
      const summaryLines = [];
      if (profile.vin) summaryLines.push('VIN: ' + profile.vin);
      els.vehiclePanelSummary.textContent = summaryLines.join('\\n');
      els.vehiclePanelSummary.style.display = summaryLines.length ? '' : 'none';

      const vinInput = getVehicleFieldInput('vin');
      if (vinInput) vinInput.classList.toggle('vehicle-suspect', vinLooksSuspicious(profile.vin));

      if (!state.vehicleAutofillResult) renderVehicleAutofillStatus(defaultVehicleStatusText(profile), Boolean(profile?.warnings?.length || vinLooksSuspicious(profile.vin)));
    }

    function applyVehicleProfileToForm(profile, { preserveStatus = false } = {}) {
      const normalized = cloneVehicleProfile(profile);
      state.vehicleProfileDraft = normalized;
      VEHICLE_PRIMARY_FIELDS.forEach((fieldName) => setVehicleFieldValue(fieldName, normalized[fieldName]));
      refreshVehiclePanel();
      if (!preserveStatus) renderVehicleAutofillStatus(defaultVehicleStatusText(normalized), Boolean(normalized.warnings?.length || vinLooksSuspicious(normalized.vin)));
    }

    function handleVehicleFieldInput(fieldName) {
      if (!fieldName) return;
      const profile = cloneVehicleProfile(state.vehicleProfileDraft || emptyVehicleProfile());
      profile[fieldName] = readVehicleFieldValue(fieldName);
      const manualFields = new Set(profile.manual_fields || []);
      const autofilledFields = new Set(profile.autofilled_fields || []);
      const tentativeFields = new Set(profile.tentative_fields || []);
      manualFields.add(fieldName);
      autofilledFields.delete(fieldName);
      tentativeFields.delete(fieldName);
      if (fieldName !== 'source_links_or_refs') profile.field_sources[fieldName] = 'manual_ui';
      profile.manual_fields = Array.from(manualFields).sort();
      profile.autofilled_fields = Array.from(autofilledFields).sort();
      profile.tentative_fields = Array.from(tentativeFields).sort();
      state.vehicleProfileDraft = profile;
      state.vehicleAutofillResult = null;
      refreshVehiclePanel();
    }

    function readVehicleProfileForm() {
      const profile = cloneVehicleProfile(state.vehicleProfileDraft || emptyVehicleProfile());
      VEHICLE_PRIMARY_FIELDS.forEach((fieldName) => {
        profile[fieldName] = readVehicleFieldValue(fieldName);
      });
      const warnings = Array.isArray(profile.warnings) ? profile.warnings.filter((item) => item !== 'VIN требует ручной проверки.') : [];
      if (vinLooksSuspicious(profile.vin)) warnings.push('VIN требует ручной проверки.');
      profile.warnings = warnings;
      state.vehicleProfileDraft = cloneVehicleProfile(profile);
      return profile;
    }

    async function copyVehicleFieldValue(fieldName) {
      const rawValue = readVehicleFieldValue(fieldName);
      const value = Array.isArray(rawValue) ? rawValue.join('\\n') : String(rawValue || '').trim();
      if (!value) {
        setStatus('НЕТ ДАННЫХ ДЛЯ КОПИРОВАНИЯ.', true);
        return;
      }
      try {
        if (navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(value);
        } else {
          const temp = document.createElement('textarea');
          temp.value = value;
          document.body.appendChild(temp);
          temp.select();
          document.execCommand('copy');
          temp.remove();
        }
        setStatus('СКОПИРОВАНО: ' + fieldName.toUpperCase(), false);
      } catch (_) {
        setStatus('НЕ УДАЛОСЬ СКОПИРОВАТЬ ПОЛЕ.', true);
      }
    }

    function buildVehicleAutofillStatus(result) {
      const profile = cloneVehicleProfile(result?.vehicle_profile || {});
      const warnings = Array.from(new Set([...(result?.warnings || []), ...(profile.warnings || [])]));
      const lines = [];
      if (profile.source_summary) lines.push('Источник: ' + profile.source_summary);
      if (result?.used_sources?.length) lines.push('Источники: ' + result.used_sources.join(' | '));
      if (profile.source_confidence !== null && profile.source_confidence !== undefined && profile.source_confidence !== '') {
        lines.push('Confidence: ' + Math.round(Number(profile.source_confidence) * 100) + '%');
      }
      if (result?.image_parse_status && result.image_parse_status !== 'not_attempted') {
        lines.push('OCR: ' + result.image_parse_status);
      }
      warnings.forEach((warning) => lines.push('! ' + warning));
      return {
        text: lines.length ? lines.join('\\n') : 'Автозаполнение отработало без замечаний.',
        isWarning: warnings.length > 0 || vinLooksSuspicious(profile.vin),
      };
    }

    async function autofillVehicleProfile() {
      const rawText = buildVehicleAutofillRawText();
      if (!rawText) {
        renderVehicleAutofillStatus('НУЖНЫ ДАННЫЕ В ПОЛЯХ КАРТОЧКИ ИЛИ В ДОП. ЗАМЕТКЕ ДЛЯ АВТОЗАПОЛНЕНИЯ.', true);
        return;
      }
      try {
        els.vehicleAutofillButton.disabled = true;
        renderVehicleAutofillStatus('АНАЛИЗИРУЮ МАРКУ, ЗАГОЛОВОК И ОПИСАНИЕ КАРТОЧКИ...', false);
        const payload = {
          raw_text: rawText,
          vehicle_profile: readVehicleProfileForm(),
          vehicle: els.cardVehicle.value.trim(),
          title: els.cardTitle.value.trim(),
          description: els.cardDescription.value.trim(),
        };
        const result = await api('/api/autofill_vehicle_data', { method: 'POST', body: payload });
        state.vehicleAutofillResult = result;
        applyVehicleProfileToForm(result.vehicle_profile || {}, { preserveStatus: true });
        if (!els.cardVehicle.value.trim() && result.card_draft?.vehicle) els.cardVehicle.value = result.card_draft.vehicle;
        if (!els.cardTitle.value.trim() && result.card_draft?.title) els.cardTitle.value = result.card_draft.title;
        if (!els.cardDescription.value.trim() && result.card_draft?.description) els.cardDescription.value = result.card_draft.description;
        syncCardDescriptionHeight();
        const status = buildVehicleAutofillStatus(result);
        renderVehicleAutofillStatus(status.text, status.isWarning);
        setStatus('ТЕХКАРТА ОБНОВЛЕНА АВТОЗАПОЛНЕНИЕМ.', false);
      } catch (error) {
        state.vehicleAutofillResult = null;
        renderVehicleAutofillStatus(error.message || 'АВТОЗАПОЛНЕНИЕ НЕ УДАЛОСЬ.', true);
        setStatus(error.message, true);
      } finally {
        els.vehicleAutofillButton.disabled = false;
      }
    }

    function currentCardPayload() {
      const vehicleProfile = readVehicleProfileForm();
      return {
        actor_name: state.actor,
        source: 'ui',
        vehicle: els.cardVehicle.value.trim(),
        title: els.cardTitle.value.trim(),
        description: els.cardDescription.value.trim(),
        column: els.cardColumn.value,
        tags: state.draftTags.map((tag) => ({ label: tag.label, color: tag.color })),
        deadline: deadlineInput(),
        vehicle_profile: vehicleProfile,
      };
    }

    function emptyRepairOrderRow() {
      return { name: '', quantity: '', price: '', total: '' };
    }

    function repairOrderParseNumber(value) {
      const normalized = String(value ?? '')
        .trim()
        .replace(/\s+/g, '')
        .replace(',', '.')
        .replace(/[^\d.\-]/g, '');
      if (!normalized) return null;
      const parsed = Number(normalized);
      return Number.isFinite(parsed) ? parsed : null;
    }

    function repairOrderRoundMoney(value) {
      return Math.round(value * 100) / 100;
    }

    function repairOrderNumberToRaw(value) {
      const rounded = repairOrderRoundMoney(value);
      if (Math.abs(rounded % 1) < 0.000001) return String(Math.trunc(rounded));
      return rounded.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
    }

    function repairOrderFormatMoney(value) {
      const normalized = typeof value === 'number' ? value : repairOrderParseNumber(value);
      const safeValue = normalized === null ? 0 : normalized;
      return new Intl.NumberFormat('ru-RU', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }).format(safeValue);
    }

    function repairOrderRowHasAnyData(row) {
      return ['name', 'quantity', 'price', 'total'].some((fieldName) => String(row?.[fieldName] ?? '').trim());
    }

    function repairOrderResolvedRowTotalValue(row) {
      const quantity = repairOrderParseNumber(row?.quantity);
      const price = repairOrderParseNumber(row?.price);
      if (quantity !== null && price !== null) {
        return repairOrderRoundMoney(quantity * price);
      }
      const fallback = repairOrderParseNumber(row?.total);
      return fallback === null ? null : repairOrderRoundMoney(fallback);
    }

    function repairOrderRowsTotalValue(rows) {
      const normalizedRows = Array.isArray(rows) ? rows.map(normalizeRepairOrderRow).filter(repairOrderRowHasAnyData) : [];
      return repairOrderRoundMoney(normalizedRows.reduce((subtotal, row) => {
        return subtotal + (repairOrderResolvedRowTotalValue(row) ?? 0);
      }, 0));
    }

    function normalizeRepairOrderRow(row) {
      const source = row && typeof row === 'object' ? row : {};
      const fallbackTotal = String(source.total ?? '').trim();
      const totalValue = repairOrderResolvedRowTotalValue({
        quantity: source.quantity,
        price: source.price,
        total: fallbackTotal,
      });
      return {
        name: String(source.name ?? '').trim(),
        quantity: String(source.quantity ?? '').trim(),
        price: String(source.price ?? '').trim(),
        total: totalValue === null ? fallbackTotal : repairOrderNumberToRaw(totalValue),
      };
    }

    function normalizeRepairOrder(order) {
      const source = order && typeof order === 'object' ? order : {};
      const normalizeRows = (rows) => Array.isArray(rows) ? rows.map(normalizeRepairOrderRow).filter(repairOrderRowHasAnyData) : [];
      return {
        number: String(source.number ?? '').trim(),
        date: String(source.date ?? '').trim(),
        status: String(source.status ?? 'open').trim().toLowerCase() === 'closed' ? 'closed' : 'open',
        opened_at: String(source.opened_at ?? source.openedAt ?? '').trim(),
        closed_at: String(source.closed_at ?? source.closedAt ?? '').trim(),
        client: String(source.client ?? '').trim(),
        phone: String(source.phone ?? '').trim(),
        vehicle: String(source.vehicle ?? '').trim(),
        license_plate: String(source.license_plate ?? '').trim(),
        vin: String(source.vin ?? '').trim(),
        mileage: String(source.mileage ?? source.odometer ?? '').trim(),
        reason: String(source.reason ?? '').trim(),
        comment: String(source.client_information ?? source.comment ?? '').trim(),
        note: String(source.note ?? source.master_comment ?? source.masterComment ?? source.internal_comment ?? source.internalComment ?? '').trim(),
        tags: normalizeRepairOrderTags(source.tags),
        works: normalizeRows(source.works),
        materials: normalizeRows(source.materials),
      };
    }

    function repairOrderHasAnyData(order) {
      const normalized = normalizeRepairOrder(order);
      return Boolean(
        normalized.number ||
        normalized.date ||
        normalized.opened_at ||
        normalized.closed_at ||
        normalized.client ||
        normalized.phone ||
        normalized.vehicle ||
        normalized.license_plate ||
        normalized.vin ||
        normalized.mileage ||
        normalized.reason ||
        normalized.comment ||
        normalized.note ||
        normalized.tags.length ||
        normalized.works.length ||
        normalized.materials.length
      );
    }

    function ensureRepairOrderRows(rows) {
      const normalized = Array.isArray(rows) ? rows.map(normalizeRepairOrderRow).filter(repairOrderRowHasAnyData) : [];
      return normalized.length ? normalized : [emptyRepairOrderRow()];
    }

    function repairOrderRowsBody(section) {
      return section === 'materials' ? els.repairOrderMaterialsBody : els.repairOrderWorksBody;
    }

    function repairOrderPadDatePart(value) {
      return String(value).padStart(2, '0');
    }

    function repairOrderCanonicalDateValue(value) {
      const normalized = String(value ?? '').trim();
      if (!normalized) return '';
      const inlineMatch = normalized.match(/^(\d{2})\.(\d{2})\.(\d{2}|\d{4})(?:[,\s]+(\d{2}):(\d{2})(?::\d{2})?)?$/);
      if (inlineMatch) {
        const yearValue = Number(inlineMatch[3]);
        const resolvedYear = inlineMatch[3].length === 2
          ? (yearValue >= 70 ? 1900 + yearValue : 2000 + yearValue)
          : yearValue;
        const hour = inlineMatch[4] || '00';
        const minute = inlineMatch[5] || '00';
        return [
          inlineMatch[1],
          inlineMatch[2],
          String(resolvedYear).padStart(4, '0'),
        ].join('.') + ' ' + hour + ':' + minute;
      }
      const parsed = new Date(normalized);
      if (Number.isNaN(parsed.getTime())) return normalized;
      return [
        repairOrderPadDatePart(parsed.getDate()),
        repairOrderPadDatePart(parsed.getMonth() + 1),
        parsed.getFullYear(),
      ].join('.') + ' ' + repairOrderPadDatePart(parsed.getHours()) + ':' + repairOrderPadDatePart(parsed.getMinutes());
    }

    function repairOrderFormDateDisplayValue(value) {
      const canonical = repairOrderCanonicalDateValue(value);
      if (!canonical) return '';
      return canonical.replace(/^(\d{2}\.\d{2}\.)\d{2}(\d{2}\s+\d{2}:\d{2})$/, '$1$2');
    }

    function currentRepairOrderDateTime() {
      const now = new Date();
      return [
        repairOrderPadDatePart(now.getDate()),
        repairOrderPadDatePart(now.getMonth() + 1),
        now.getFullYear(),
      ].join('.') + ' ' + repairOrderPadDatePart(now.getHours()) + ':' + repairOrderPadDatePart(now.getMinutes());
    }

    function repairOrderDateDisplayValue(value) {
      return repairOrderCanonicalDateValue(value);
    }

    function repairOrderListDateDisplayValue(value) {
      return repairOrderCanonicalDateValue(value);
    }

    function repairOrderStatusLabel(status) {
      return String(status || '').trim().toLowerCase() === 'closed' ? 'Закрыт' : 'Открыт';
    }

    function repairOrderCardDefaults(card) {
      const currentCard = card && typeof card === 'object' ? card : {};
      const profile = currentCard.vehicle_profile && typeof currentCard.vehicle_profile === 'object' ? currentCard.vehicle_profile : {};
      const openedAt = formatDate(currentCard.created_at || '') || currentRepairOrderDateTime();
      return normalizeRepairOrder({
        date: openedAt,
        opened_at: openedAt,
        client: profile.customer_name || '',
        phone: profile.customer_phone || '',
        vehicle: currentCard.vehicle || [profile.make_display, profile.model_display].filter(Boolean).join(' '),
        license_plate: currentCard.repair_order?.license_plate || '',
        vin: profile.vin || currentCard.repair_order?.vin || '',
        mileage: currentCard.repair_order?.mileage || '',
        reason: currentCard.title || '',
        comment: currentCard.description || '',
        note: currentCard.repair_order?.note || '',
        tags: currentCard.repair_order?.tags || [],
      });
    }

    function repairOrderCardDraft(card, order = {}) {
      const source = order && typeof order === 'object' ? order : {};
      const defaults = repairOrderCardDefaults(card);
      const normalized = normalizeRepairOrder(source);
      const hasField = (fieldName, aliases = []) => [fieldName].concat(aliases).some((key) => Object.prototype.hasOwnProperty.call(source, key));
      const resolvedField = (fieldName, aliases = []) => hasField(fieldName, aliases) ? normalized[fieldName] : defaults[fieldName];
      return normalizeRepairOrder({
        ...normalized,
        date: resolvedField('date'),
        opened_at: resolvedField('opened_at', ['openedAt']),
        closed_at: resolvedField('closed_at', ['closedAt']),
        client: resolvedField('client'),
        phone: resolvedField('phone'),
        vehicle: resolvedField('vehicle'),
        license_plate: resolvedField('license_plate', ['licensePlate']),
        vin: resolvedField('vin'),
        mileage: resolvedField('mileage', ['odometer']),
        reason: resolvedField('reason'),
        comment: resolvedField('comment', ['client_information', 'clientInformation']),
        note: resolvedField('note', ['master_comment', 'masterComment', 'internal_comment', 'internalComment']),
        tags: hasField('tags') ? normalized.tags : defaults.tags,
        works: normalized.works,
        materials: normalized.materials,
      });
    }

    function repairOrderHeadingLegacy(number) {
      const normalizedNumber = String(number ?? '').trim();
      return normalizedNumber ? ('Р—РђРљРђР—-РќРђР РЇР” в„–' + normalizedNumber) : 'Р—РђРљРђР—-РќРђР РЇР”';
    }

    function repairOrderCardRequiredMessageLegacy() {
      return 'РЎРЅР°С‡Р°Р»Р° СЃРѕС…СЂР°РЅРёС‚Рµ РєР°СЂС‚РѕС‡РєСѓ, С‡С‚РѕР±С‹ РѕС‚РєСЂС‹С‚СЊ Р·Р°РєР°Р·-РЅР°СЂСЏРґ.';
    }

    function repairOrderHeading(number) {
      const normalizedNumber = String(number ?? '').trim();
      return normalizedNumber ? ('ЗАКАЗ-НАРЯД №' + normalizedNumber) : 'ЗАКАЗ-НАРЯД';
    }

    function repairOrderCardRequiredMessage() {
      return 'Сначала сохраните карточку, чтобы открыть заказ-наряд.';
    }

    function repairOrderResponseCard(data, fallbackOrder = {}) {
      return data?.card || {
        ...(state.activeCard || {}),
        id: state.editingId,
        repair_order: data?.repair_order || fallbackOrder,
      };
    }

    async function ensureRepairOrderCard() {
      if (state.editingId && state.activeCard?.id) return state.activeCard;
      if (state.editingId) return { id: state.editingId, repair_order: state.activeCard?.repair_order || {} };
      const payload = currentCardPayload();
      if (!payload.title) {
        const repairOrder = readRepairOrderFromForm();
        payload.title = repairOrder.reason || repairOrder.vehicle || 'Заказ-наряд';
        if (!payload.vehicle && repairOrder.vehicle) payload.vehicle = repairOrder.vehicle;
      }
      if (!payload.title) {
        setStatus(CARD_TITLE_REQUIRED_MESSAGE, true);
        els.cardTitle.focus();
        return null;
      }
      const data = await persistCardPayload(payload);
      const savedCard = data?.card || null;
      if (!savedCard?.id) {
        setStatus(repairOrderCardRequiredMessage(), true);
        return null;
      }
      applyCardModalState(savedCard);
      await refreshSnapshot(true);
      return savedCard;
    }

    async function requireRepairOrderCardId() {
      if (state.editingId) return state.editingId;
      const preparedCard = await ensureRepairOrderCard();
      return preparedCard?.id || '';
    }

    function applyRepairOrderCardUpdate(updatedCard, fallbackOrder = {}) {
      state.activeCard = updatedCard;
      const nextOrder = repairOrderCardDraft(updatedCard, updatedCard?.repair_order || fallbackOrder);
      applyRepairOrderToForm(nextOrder);
      refreshRepairOrderEntry(updatedCard);
      return nextOrder;
    }

    function repairOrderRowInputHtml(fieldName, value, placeholder = '') {
      const isNumeric = fieldName === 'quantity' || fieldName === 'price';
      return '<input class="repair-order-table__input' + (isNumeric ? ' repair-order-table__input--num' : '') + '" type="text"' + (isNumeric ? ' inputmode="decimal"' : '') + ' data-repair-order-cell="' + escapeHtml(fieldName) + '" value="' + escapeHtml(value) + '" placeholder="' + escapeHtml(placeholder) + '">';
    }

    function repairOrderRowHtml(section, row, index) {
      const normalized = normalizeRepairOrderRow(row);
      const totalValue = repairOrderResolvedRowTotalValue(normalized);
      const hasDisplayTotal = totalValue !== null || Boolean(normalized.total);
      return '<tr data-repair-order-row="' + escapeHtml(section) + '" data-repair-order-total-raw="' + escapeHtml(normalized.total) + '">' +
        '<td>' + repairOrderRowInputHtml('name', normalized.name, 'Наименование') + '</td>' +
        '<td class="repair-order-table__numeric">' + repairOrderRowInputHtml('quantity', normalized.quantity, '1') + '</td>' +
        '<td class="repair-order-table__numeric">' + repairOrderRowInputHtml('price', normalized.price, '0') + '</td>' +
        '<td class="repair-order-table__numeric"><div class="repair-order-cell-total" data-repair-order-row-total data-empty="' + (hasDisplayTotal ? 'false' : 'true') + '">' + escapeHtml(hasDisplayTotal ? repairOrderFormatMoney(totalValue ?? normalized.total) : '-') + '</div></td>' +
        '<td class="repair-order-table__action"><button class="btn btn--ghost repair-order-row-remove" type="button" data-remove-repair-order-row="' + escapeHtml(section) + '" data-row-index="' + escapeHtml(index) + '">&times;</button></td>' +
        '</tr>';
    }

    function renderRepairOrderRows(section, rows) {
      const body = repairOrderRowsBody(section);
      body.innerHTML = ensureRepairOrderRows(rows).map((row, index) => repairOrderRowHtml(section, row, index)).join('');
      syncRepairOrderTotals();
    }

    function readRepairOrderRowElement(row) {
      return normalizeRepairOrderRow({
        name: row.querySelector('[data-repair-order-cell="name"]')?.value,
        quantity: row.querySelector('[data-repair-order-cell="quantity"]')?.value,
        price: row.querySelector('[data-repair-order-cell="price"]')?.value,
        total: row.dataset.repairOrderTotalRaw || '',
      });
    }

    function readRepairOrderRows(section) {
      const body = repairOrderRowsBody(section);
      return Array.from(body.querySelectorAll('tr[data-repair-order-row]')).map((row) => readRepairOrderRowElement(row)).filter(repairOrderRowHasAnyData);
    }

    function syncRepairOrderSectionTotals(section) {
      const body = repairOrderRowsBody(section);
      let subtotal = 0;
      Array.from(body.querySelectorAll('tr[data-repair-order-row]')).forEach((row) => {
        const normalized = readRepairOrderRowElement(row);
        const totalValue = repairOrderResolvedRowTotalValue(normalized);
        const hasDisplayTotal = totalValue !== null || Boolean(normalized.total);
        row.dataset.repairOrderTotalRaw = normalized.total;
        const totalCell = row.querySelector('[data-repair-order-row-total]');
        if (totalCell) {
          totalCell.textContent = hasDisplayTotal ? repairOrderFormatMoney(totalValue ?? normalized.total) : '-';
          totalCell.dataset.empty = hasDisplayTotal ? 'false' : 'true';
        }
        subtotal += totalValue ?? (repairOrderParseNumber(normalized.total) ?? 0);
      });
      const roundedSubtotal = repairOrderRoundMoney(subtotal);
      document.querySelectorAll('[data-repair-order-total="' + section + '"]').forEach((node) => {
        node.textContent = repairOrderFormatMoney(roundedSubtotal);
      });
      return roundedSubtotal;
    }

    function syncRepairOrderTotals() {
      const worksTotal = syncRepairOrderSectionTotals('works');
      const materialsTotal = syncRepairOrderSectionTotals('materials');
      const grandTotal = repairOrderRoundMoney(worksTotal + materialsTotal);
      document.querySelectorAll('[data-repair-order-total="grand"]').forEach((node) => {
        node.textContent = repairOrderFormatMoney(grandTotal);
      });
    }

    function syncRepairOrderStatusUi(status) {
      const normalizedStatus = String(status || '').trim().toLowerCase() === 'closed' ? 'closed' : 'open';
      els.repairOrderStatus.textContent = repairOrderStatusLabel(normalizedStatus);
      els.repairOrderStatus.dataset.status = normalizedStatus;
      els.repairOrderCloseButton.textContent = normalizedStatus === 'closed' ? 'ОТКРЫТЬ ЗАКАЗ-НАРЯД' : 'ЗАКРЫТЬ ЗАКАЗ-НАРЯД';
    }

    function applyRepairOrderToForm(order) {
      const normalized = repairOrderCardDraft(state.activeCard, order);
      els.repairOrderNumber.value = normalized.number;
      els.repairOrderDate.value = repairOrderFormDateDisplayValue(normalized.date || currentRepairOrderDateTime());
      els.repairOrderOpenedAt.value = repairOrderFormDateDisplayValue(normalized.opened_at || normalized.date || currentRepairOrderDateTime());
      els.repairOrderClosedAt.value = repairOrderFormDateDisplayValue(normalized.closed_at);
      els.repairOrderClient.value = normalized.client;
      els.repairOrderPhone.value = normalized.phone;
      els.repairOrderVehicle.value = normalized.vehicle;
      els.repairOrderLicensePlate.value = normalized.license_plate;
      els.repairOrderVin.value = normalized.vin;
      els.repairOrderMileage.value = normalized.mileage;
      els.repairOrderReason.value = normalized.reason;
      els.repairOrderComment.value = normalized.comment;
      els.repairOrderNote.value = normalized.note;
      state.repairOrderTags = normalizeRepairOrderTags(normalized.tags);
      state.repairOrderTagColor = state.repairOrderTags[0]?.color || 'green';
      renderRepairOrderTags();
      renderRepairOrderRows('works', normalized.works);
      renderRepairOrderRows('materials', normalized.materials);
      const heading = repairOrderHeading(normalized.number);
      els.repairOrderModalTitle.textContent = heading;
      els.repairOrderModalTitle.title = heading;
      syncRepairOrderStatusUi(normalized.status);
      syncRepairOrderTotals();
    }

    function readRepairOrderFromForm() {
      return normalizeRepairOrder({
        number: els.repairOrderNumber.value,
        date: repairOrderCanonicalDateValue(els.repairOrderDate.value),
        status: els.repairOrderStatus.dataset.status || 'open',
        opened_at: repairOrderCanonicalDateValue(els.repairOrderOpenedAt.value),
        closed_at: repairOrderCanonicalDateValue(els.repairOrderClosedAt.value),
        client: els.repairOrderClient.value,
        phone: els.repairOrderPhone.value,
        vehicle: els.repairOrderVehicle.value,
        license_plate: els.repairOrderLicensePlate.value,
        vin: els.repairOrderVin.value,
        mileage: els.repairOrderMileage.value,
        reason: els.repairOrderReason.value,
        comment: els.repairOrderComment.value,
        client_information: els.repairOrderComment.value,
        note: els.repairOrderNote.value,
        tags: state.repairOrderTags,
        works: readRepairOrderRows('works'),
        materials: readRepairOrderRows('materials'),
      });
    }

    function refreshRepairOrderEntry(card = state.activeCard) {
      const currentCard = card || null;
      const order = repairOrderCardDraft(currentCard, currentCard?.repair_order || {});
      els.repairOrderButton.disabled = false;
      els.repairOrderButton.textContent = repairOrderHeading(order.number);
    }

    function openRepairOrderModal() {
      const order = repairOrderCardDraft(state.activeCard, state.activeCard?.repair_order || {});
      applyRepairOrderToForm(order);
      els.repairOrderModal.classList.add('is-open');
    }

    function closeRepairOrderModal() {
      els.repairOrderModal.classList.remove('is-open');
    }

    function addRepairOrderRow(section) {
      const body = repairOrderRowsBody(section);
      const rowIndex = body.querySelectorAll('tr[data-repair-order-row]').length;
      body.insertAdjacentHTML('beforeend', repairOrderRowHtml(section, emptyRepairOrderRow(), rowIndex));
      syncRepairOrderTotals();
      body.querySelector('tr:last-child input')?.focus();
    }

    function removeRepairOrderRow(section, rowIndex) {
      const rows = ensureRepairOrderRows(readRepairOrderRows(section));
      if (rowIndex >= 0 && rowIndex < rows.length) rows.splice(rowIndex, 1);
      renderRepairOrderRows(section, rows);
    }

    function buildRepairOrderPrintTable(title, rows, subtotalLabel) {
      const normalizedRows = Array.isArray(rows) ? rows.map(normalizeRepairOrderRow).filter(repairOrderRowHasAnyData) : [];
      const body = normalizedRows.length ? normalizedRows.map((row) => {
        const totalValue = repairOrderResolvedRowTotalValue(row);
        return '<tr>' +
          '<td>' + escapeHtml(row.name) + '</td>' +
          '<td class="print-table__numeric">' + escapeHtml(row.quantity) + '</td>' +
          '<td class="print-table__numeric">' + escapeHtml(row.price) + '</td>' +
          '<td class="print-table__numeric">' + escapeHtml(totalValue === null ? row.total : repairOrderFormatMoney(totalValue)) + '</td>' +
          '</tr>';
      }).join('') : '<tr><td class="print-table__empty" colspan="4">Нет позиций</td></tr>';
      const subtotal = repairOrderRowsTotalValue(normalizedRows);
      return '<section class="print-section">' +
        '<h2>' + escapeHtml(title) + '</h2>' +
        '<table class="print-table">' +
        '<thead><tr><th>Наименование</th><th>Количество</th><th>Цена</th><th>Сумма</th></tr></thead>' +
        '<tbody>' + body + '</tbody>' +
        '<tfoot><tr class="print-table__summary"><td colspan="3">' + escapeHtml(subtotalLabel) + '</td><td class="print-table__numeric">' + escapeHtml(repairOrderFormatMoney(subtotal)) + '</td></tr></tfoot>' +
        '</table>' +
        '</section>';
    }

    function buildRepairOrderPrintHtml(order) {
      const normalized = normalizeRepairOrder(order);
      const worksTotal = repairOrderRowsTotalValue(normalized.works);
      const materialsTotal = repairOrderRowsTotalValue(normalized.materials);
      const grandTotal = repairOrderRoundMoney(worksTotal + materialsTotal);
      const comment = escapeHtml(normalized.comment).replace(/\n/g, '<br>');
      return [
        '<!doctype html>',
        '<html lang="ru"><head><meta charset="utf-8"><title>Заказ-наряд</title><style>',
        '@page { size: A4; margin: 12mm; }',
        'html, body { margin: 0; padding: 0; background: #ffffff; color: #000000; font-family: "Segoe UI", Arial, sans-serif; font-size: 12px; line-height: 1.45; }',
        'body { padding: 0; }',
        '.sheet { width: 100%; min-height: 100%; }',
        '.sheet__head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 14px; }',
        '.sheet__title { margin: 0; font-size: 22px; font-weight: 700; }',
        '.sheet__number { margin-top: 4px; font-size: 13px; }',
        '.meta-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-bottom: 14px; }',
        '.meta-card { border: 1px solid #000000; padding: 8px 10px; min-height: 58px; }',
        '.meta-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; color: #444444; margin-bottom: 6px; }',
        '.meta-value { white-space: pre-wrap; word-break: break-word; }',
        '.print-section { margin-bottom: 14px; }',
        '.print-section h2 { margin: 0 0 8px; font-size: 14px; font-weight: 700; }',
        '.print-comment { border: 1px solid #000000; min-height: 54px; padding: 8px 10px; white-space: pre-wrap; }',
        '.print-table { width: 100%; border-collapse: collapse; table-layout: fixed; }',
        '.print-table th, .print-table td { border: 1px solid #000000; padding: 7px 8px; vertical-align: top; text-align: left; }',
        '.print-table th { font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; }',
        '.print-table__numeric { text-align: right !important; font-variant-numeric: tabular-nums; }',
        '.print-table__empty { text-align: center; color: #555555; }',
        '.print-table__summary td { font-weight: 700; }',
        '.print-totals { margin-top: 10px; margin-left: auto; width: 280px; border: 1px solid #000000; }',
        '.print-totals__row { display: flex; justify-content: space-between; gap: 12px; padding: 8px 10px; border-bottom: 1px solid #000000; }',
        '.print-totals__row:last-child { border-bottom: 0; }',
        '.print-totals__row span:last-child { font-variant-numeric: tabular-nums; }',
        '.print-totals__row--grand { font-size: 16px; font-weight: 700; }',
        '</style></head><body><div class="sheet">',
        '<header class="sheet__head"><div><h1 class="sheet__title">Заказ-наряд</h1><div class="sheet__number">№ ' + escapeHtml(normalized.number || '—') + '</div></div><div class="sheet__number">Дата: ' + escapeHtml(normalized.date || '—') + '</div></header>',
        '<section class="meta-grid">',
        '<div class="meta-card"><div class="meta-label">Клиент</div><div class="meta-value">' + escapeHtml(normalized.client) + '</div></div>',
        '<div class="meta-card"><div class="meta-label">Телефон</div><div class="meta-value">' + escapeHtml(normalized.phone) + '</div></div>',
        '<div class="meta-card"><div class="meta-label">Автомобиль</div><div class="meta-value">' + escapeHtml(normalized.vehicle) + '</div></div>',
        '<div class="meta-card"><div class="meta-label">Госномер</div><div class="meta-value">' + escapeHtml(normalized.license_plate) + '</div></div>',
        '</section>',
        '<section class="print-section"><h2>Информация для клиента</h2><div class="print-comment">' + comment + '</div></section>',
        buildRepairOrderPrintTable('Работы', normalized.works, 'Итого работы'),
        buildRepairOrderPrintTable('Материалы', normalized.materials, 'Итого материалы'),
        '<section class="print-totals">' +
        '<div class="print-totals__row"><span>Итого работы</span><span>' + escapeHtml(repairOrderFormatMoney(worksTotal)) + '</span></div>' +
        '<div class="print-totals__row"><span>Итого материалы</span><span>' + escapeHtml(repairOrderFormatMoney(materialsTotal)) + '</span></div>' +
        '<div class="print-totals__row print-totals__row--grand"><span>Итого к оплате</span><span>' + escapeHtml(repairOrderFormatMoney(grandTotal)) + '</span></div>' +
        '</section>',
        '</div></body></html>',
      ].join('');
    }

    function openRepairOrderPrint(order) {
      const printWindow = window.open('', '_blank', 'noopener,noreferrer,width=1024,height=1360');
      if (!printWindow) {
        setStatus('Не удалось открыть окно печати.', true);
        return;
      }
      printWindow.document.open();
      printWindow.document.write(buildRepairOrderPrintHtml(order));
      printWindow.document.close();
      window.setTimeout(() => {
        try {
          printWindow.focus();
          printWindow.print();
        } catch (_) {
          setStatus('Не удалось запустить печать.', true);
        }
      }, 120);
    }

    async function persistRepairOrderRecord({ statusMessage = '', silent = false } = {}) {
      const cardId = await requireRepairOrderCardId();
      if (!cardId) return null;
      const repairOrder = readRepairOrderFromForm();
      const data = await api('/api/update_repair_order', {
        method: 'POST',
        body: {
          card_id: cardId,
          actor_name: state.actor,
          source: 'ui',
          repair_order: repairOrder,
        },
      });
      const updatedCard = repairOrderResponseCard(data, repairOrder);
      const nextOrder = applyRepairOrderCardUpdate(updatedCard, data?.repair_order || repairOrder);
      if (!silent && statusMessage) setStatus(statusMessage, false);
      return { cardId, repairOrder: nextOrder, card: updatedCard, data };
    }

    async function saveRepairOrder(printAfter = false) {
      const cardId = await requireRepairOrderCardId();
      if (!cardId) return;
      const repairOrder = readRepairOrderFromForm();
      try {
        const data = await api('/api/update_card', {
          method: 'POST',
          body: {
            card_id: cardId,
            actor_name: state.actor,
            source: 'ui',
            repair_order: repairOrder,
          },
        });
        const updatedCard = repairOrderResponseCard(data, repairOrder);
        const nextOrder = applyRepairOrderCardUpdate(updatedCard, repairOrder);
        setStatus('Заказ-наряд сохранён.', false);
        if (printAfter) openRepairOrderPrint(nextOrder);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function autofillRepairOrder() {
      const cardId = await requireRepairOrderCardId();
      if (!cardId) return;
      try {
        els.repairOrderAutofillButton.disabled = true;
        const data = await api('/api/autofill_repair_order', {
          method: 'POST',
          body: {
            card_id: cardId,
            actor_name: state.actor,
            source: 'ui',
            overwrite: false,
          },
        });
        const updatedCard = repairOrderResponseCard(data);
        applyRepairOrderCardUpdate(updatedCard, data?.repair_order || {});
        setStatus(buildRepairOrderAutofillStatus(data), false);
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        els.repairOrderAutofillButton.disabled = false;
      }
    }

    function buildRepairOrderAutofillStatus(data) {
      if (!data?.meta?.changed) return 'Пустые поля для автозаполнения не найдены.';
      const report = data?.meta?.autofill_report || {};
      const parts = [];
      const worksSuggested = Number(report.works_suggested || 0);
      const materialsSuggested = Number(report.materials_suggested || 0);
      const priceHits = Array.isArray(report.prices_applied) ? report.prices_applied.length : 0;
      if (worksSuggested > 0) parts.push('работы ' + worksSuggested);
      if (materialsSuggested > 0) parts.push('материалы ' + materialsSuggested);
      if (priceHits > 0) parts.push('цены из истории ' + priceHits);
      const reviewItems = Array.isArray(report.review_items) ? report.review_items.filter(Boolean) : [];
      if (!parts.length) return 'Заказ-наряд автозаполнен.';
      return 'Заказ-наряд автозаполнен: ' + parts.join(', ') + '.' + (reviewItems.length ? ' ' + reviewItems[0] : '');
    }

    saveRepairOrder = async function(printAfter = false) {
      try {
        const persisted = await persistRepairOrderRecord({ statusMessage: 'Заказ-наряд сохранён.' });
        if (!persisted) return;
        if (printAfter) setStatus('Печать будет добавлена позже. Заказ-наряд сохранён.', false);
      } catch (error) {
        setStatus(error.message, true);
      }
    };

    async function toggleRepairOrderStatus() {
      try {
        const persisted = await persistRepairOrderRecord({ silent: true });
        if (!persisted) return;
        const currentStatus = readRepairOrderFromForm().status;
        const nextStatus = currentStatus === 'closed' ? 'open' : 'closed';
        els.repairOrderCloseButton.disabled = true;
        const data = await api('/api/set_repair_order_status', {
          method: 'POST',
          body: {
            card_id: persisted.cardId,
            status: nextStatus,
            actor_name: state.actor,
            source: 'ui',
          },
        });
        const updatedCard = repairOrderResponseCard(data, persisted.repairOrder);
        applyRepairOrderCardUpdate(updatedCard, data?.repair_order || persisted.repairOrder);
        await loadRepairOrders(false);
        setStatus(nextStatus === 'closed' ? 'Заказ-наряд закрыт.' : 'Заказ-наряд открыт.', false);
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        els.repairOrderCloseButton.disabled = false;
      }
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

    function applyCardModalState(card) {
      const currentCard = card || null;
      state.activeCard = currentCard;
      state.editingId = currentCard?.id || null;
      state.vehicleAutofillResult = null;
      state.draftTags = normalizeDraftTags(currentCard?.tag_items || currentCard?.tags || []);
      state.draftTagColor = 'green';
      const modalHeading = currentCard?.id ? cardHeading(currentCard) : 'Новая карточка';
      els.cardModalTitle.textContent = limitCardModalHeading(modalHeading);
      els.cardModalTitle.title = modalHeading;
      els.cardVehicle.value = currentCard?.vehicle || '';
      els.cardTitle.value = currentCard?.title || '';
      els.cardDescription.value = currentCard?.description || '';
      populateColumns(currentCard?.column || state.snapshot?.columns?.[0]?.id);
      const parts = secondsToParts(currentCard?.remaining_seconds || 86400);
      els.signalDays.value = parts.days;
      els.signalHours.value = parts.hours;
      renderSignalPreview();
      els.cardMetaLine.textContent = currentCard?.id ? ('СОЗДАНО: ' + formatDate(currentCard.created_at) + ' · ИЗМЕНЕНО: ' + formatDate(currentCard.updated_at)) : 'НОВАЯ ЗАПИСЬ';
      els.archiveAction.classList.toggle('hidden', !currentCard?.id || currentCard.archived);
      els.restoreAction.classList.toggle('hidden', !currentCard?.id || !currentCard.archived);
      state.vehicleProfileBaseline = cloneVehicleProfile(currentCard?.vehicle_profile || {});
      applyVehicleProfileToForm(currentCard?.vehicle_profile || emptyVehicleProfile());
      refreshRepairOrderEntry(currentCard);
      renderColorTags();
      renderFiles(currentCard);
      renderLogs([]);
    }

    function resetCardModalState() {
      state.activeCard = null;
      state.editingId = null;
      state.vehicleProfileDraft = null;
      state.vehicleProfileBaseline = null;
      state.vehicleAutofillResult = null;
      state.draftTags = [];
      state.draftTagColor = 'green';
      refreshRepairOrderEntry(null);
      els.fileInput.value = '';
      syncFileDropzone(null);
    }

    async function persistCardPayload(payload) {
      if (state.editingId) {
        return api('/api/update_card', { method: 'POST', body: { card_id: state.editingId, ...payload } });
      }
      return api('/api/create_card', { method: 'POST', body: payload });
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

    function renderColorTags() {
      const atLimit = state.draftTags.length >= CARD_TAG_LIMIT;
      renderTagColorPicker();
      els.tagList.innerHTML = state.draftTags.length
        ? state.draftTags.map((tag) => '<button class="tag" data-tag-color="' + escapeHtml(tag.color) + '" data-remove-tag="' + escapeHtml(tag.label) + '"><span class="tag__dot"></span>' + escapeHtml(tag.label) + ' ×</button>').join('')
        : '<div class="tag tag--muted">МЕТОК НЕТ</div>';
      if (els.tagMeta) {
        els.tagMeta.textContent = state.draftTags.length + ' / ' + CARD_TAG_LIMIT;
        els.tagMeta.dataset.limitState = atLimit ? 'full' : 'open';
      }
      els.tagInput.disabled = atLimit;
      els.tagInput.placeholder = atLimit ? 'ЛИМИТ 3 / 3' : 'ЖДЁМ';
      els.tagAddButton.disabled = atLimit;
      els.tagSuggestions.innerHTML = SUGGESTED_TAGS.map((tag) => {
        const active = state.draftTags.some((item) => item.label === tag.label);
        const disabled = atLimit && !active;
        const activeClass = active ? ' is-active' : '';
        const disabledClass = disabled ? ' is-disabled' : '';
        const disabledAttr = disabled ? ' disabled' : '';
        return '<button class="tag-suggestion' + activeClass + disabledClass + '" data-tag-color="' + escapeHtml(tag.color || 'green') + '" data-suggest-tag="' + escapeHtml(tag.label) + '" data-suggest-color="' + escapeHtml(tag.color || 'green') + '"' + disabledAttr + '>' + escapeHtml(tag.label) + '</button>';
      }).join('');
    }

    function syncFileDropzone(card = state.activeCard) {
      const canUpload = Boolean(card?.id || state.editingId);
      els.fileDropzone.classList.toggle('is-disabled', !canUpload);
      els.fileDropzone.classList.remove('is-active');
      els.fileDropzone.setAttribute('aria-disabled', canUpload ? 'false' : 'true');
      els.fileDropzone.setAttribute('contenteditable', canUpload ? 'plaintext-only' : 'false');
      els.fileDropzone.dataset.title = canUpload ? 'ПЕРЕНЕСИТЕ ИЛИ ВСТАВЬТЕ ФАЙЛ' : 'СНАЧАЛА СОХРАНИТЕ КАРТОЧКУ';
      els.fileDropzone.dataset.hint = canUpload
        ? 'Ctrl+V, правый клик -> Вставить, drag-and-drop или клик для выбора. TXT, PDF, Word, Excel.'
        : 'Без сохранённой карточки вложения не принимаются.';
      els.fileDropzone.textContent = '';
    }

    function renderFiles(card) {
      const attachments = (card?.attachments || []).filter((item) => !item.removed);
      syncFileDropzone(card);
      els.fileDropMeta.textContent = card?.id
        ? (attachments.length
            ? ('ВЛОЖЕНИЙ: ' + attachments.length + '. ПЕРЕТАЩИТЕ, ВСТАВЬТЕ ИЛИ КЛИКНИТЕ ПО ПОЛЮ ДЛЯ ДОБАВЛЕНИЯ.')
            : 'ПОЛЕ ПУСТО. ПЕРЕТАЩИТЕ ФАЙЛ, НАЖМИТЕ CTRL+V ИЛИ ВСТАВЬТЕ ЧЕРЕЗ КОНТЕКСТНОЕ МЕНЮ.')
        : 'СНАЧАЛА СОХРАНИТЕ КАРТОЧКУ, ЗАТЕМ ДОБАВЛЯЙТЕ ВЛОЖЕНИЯ.';
      els.fileList.innerHTML = attachments.length
        ? attachments.map((item) => '<div class="file-row"><div>' + escapeHtml(item.file_name) + '</div><div class="log-row__meta">' + escapeHtml(formatDate(item.created_at)) + ' · ' + Math.round(item.size_bytes / 1024) + ' КБ</div><div style="display:flex; gap:8px; flex-wrap:wrap;"><a class="btn" href="/api/attachment?card_id=' + encodeURIComponent(card.id) + '&attachment_id=' + encodeURIComponent(item.id) + '">СКАЧАТЬ</a><button class="btn btn--danger" data-remove-file="' + escapeHtml(item.id) + '">УДАЛИТЬ</button></div></div>').join('')
        : '<div class="log-row__meta">ФАЙЛОВ НЕТ.</div>';
    }

    function requireSavedCardForFiles({ syncDropzone = false } = {}) {
      if (state.editingId) return true;
      setStatus('СНАЧАЛА СОХРАНИТЕ КАРТОЧКУ.', true);
      if (syncDropzone) syncFileDropzone(null);
      return false;
    }

    async function refreshActiveCardFiles() {
      if (!state.editingId) return null;
      const data = await api('/api/get_card?card_id=' + encodeURIComponent(state.editingId));
      state.activeCard = data.card;
      renderFiles(data.card);
      return data.card;
    }

    async function removeActiveCardAttachment(attachmentId) {
      await api('/api/remove_card_attachment', {
        method: 'POST',
        body: { card_id: state.editingId, attachment_id: attachmentId, actor_name: state.actor, source: 'ui' },
      });
      await refreshActiveCardFiles();
      await refreshSnapshot(true);
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

    function legacyRenderRepairOrderRowsExpandedShadow(items) {
      return items.map((item) => '<div class="archive-row repair-orders-row" role="button" tabindex="0" data-open-repair-order-card="' + escapeHtml(item.card_id) + '" title="РћС‚РєСЂС‹С‚СЊ Р·Р°РєР°Р·-РЅР°СЂСЏРґ"><div class="repair-orders-row__number">в„– ' + escapeHtml(item.number || '-') + '</div><div class="repair-orders-row__vehicle" title="' + escapeHtml(item.vehicle || '-') + '">' + escapeHtml(item.vehicle || 'РђРІС‚Рѕ РЅРµ СѓРєР°Р·Р°РЅРѕ') + '</div><div class="repair-orders-row__title" title="' + escapeHtml(item.heading || 'Р—Р°РєР°Р·-РЅР°СЂСЏРґ') + '">' + escapeHtml(item.heading || 'Р—Р°РєР°Р·-РЅР°СЂСЏРґ') + '</div></div>').join('');
    }

    function renderRepairOrders(data) {
      const items = data?.repair_orders || [];
      const meta = data?.meta || {};
      els.repairOrdersMeta.textContent =
        'ПОКАЗАНО: ' + items.length +
        ' | ВСЕГО: ' + (meta.total ?? items.length) +
        ' | СПИСОК: НОМЕР / МАРКА / ЗАГОЛОВОК';
      els.repairOrdersList.innerHTML = items.length
        ? items.map((item) => '<div class="archive-row repair-orders-row" role="button" tabindex="0" data-open-repair-order-card="' + escapeHtml(item.card_id) + '" title="Открыть заказ-наряд"><div class="repair-orders-row__number">№ ' + escapeHtml(item.number || '-') + '</div><div class="repair-orders-row__vehicle" title="' + escapeHtml(item.vehicle || '-') + '">' + escapeHtml(item.vehicle || 'Авто не указано') + '</div><div class="repair-orders-row__title" title="' + escapeHtml(item.heading || 'Заказ-наряд') + '">' + escapeHtml(item.heading || 'Заказ-наряд') + '</div></div>').join('')
        : '<div class="log-row__meta">ЗАКАЗ-НАРЯДОВ ПОКА НЕТ.</div>';
    }

    renderArchive = function() {
      const cards = state.snapshot?.archive || [];
      els.archiveList.innerHTML = cards.length
        ? cards.map((card) => {
            const heading = cardHeading(card);
            const compactDescription = String(card.description || 'Описание не указано').replace(/\s+/g, ' ').trim();
            const summary = compactDescription.length > 180 ? compactDescription.slice(0, 177) + '...' : compactDescription;
            return '<div class="archive-row archive-row--compact"><div class="archive-row__main"><div class="archive-row__title" title="' + escapeHtml(heading) + '">' + escapeHtml(heading) + '</div><div class="archive-row__summary" title="' + escapeHtml(compactDescription || 'Описание не указано') + '">' + escapeHtml(summary || 'Описание не указано') + '</div></div><div class="archive-row__side"><div class="archive-row__meta">АРХИВ: ' + escapeHtml(formatDate(card.updated_at)) + '</div><button class="btn" data-restore-card="' + escapeHtml(card.id) + '">ВЕРНУТЬ</button></div></div>';
          }).join('')
        : '<div class="log-row__meta">АРХИВ ПУСТ.</div>';
    };

function renderCompactArchiveRows(cards) {
      return cards.map((card) => {
        const heading = cardHeading(card);
        const compactDescription = String(card.description || 'Описание не указано').replace(/\s+/g, ' ').trim();
        const summary = compactDescription.length > 180 ? compactDescription.slice(0, 177) + '...' : compactDescription;
        return '<div class="archive-row archive-row--compact"><div class="archive-row__main"><div class="archive-row__title" title="' + escapeHtml(heading) + '">' + escapeHtml(heading) + '</div><div class="archive-row__summary" title="' + escapeHtml(compactDescription || 'Описание не указано') + '">' + escapeHtml(summary || 'Описание не указано') + '</div></div><div class="archive-row__side"><div class="archive-row__meta">АРХИВ: ' + escapeHtml(formatDate(card.updated_at)) + '</div><button class="btn" data-restore-card="' + escapeHtml(card.id) + '">ВЕРНУТЬ</button></div></div>';
      }).join('');
    }

    function legacyRepairOrdersMetaTextExpandedShadow(items, meta) {
      return 'ПОКАЗАНО: ' + items.length +
        ' | ВСЕГО: ' + (meta.total ?? items.length) +
        ' | СПИСОК: НОМЕР / МАРКА / ЗАГОЛОВОК';
    }

    function gptWallMetaText(meta) {
      return 'СОБРАНО: ' + formatDate(meta.generated_at) +
        ' | СТОЛБЦОВ: ' + (meta.columns ?? 0) +
        ' | АКТИВНЫХ: ' + (meta.active_cards ?? 0) +
        ' | АРХИВ: ' + (meta.archived_cards ?? 0) +
        ' | СТИКЕРОВ: ' + (meta.stickies ?? 0) +
        ' | СОБЫТИЙ: ' + (meta.events_total ?? 0);
    }

    function normalizeGptWallView(value) {
      return value === 'event_log' ? 'event_log' : 'board_content';
    }

    function buildGptWallEventsFallback(data) {
      const events = Array.isArray(data?.events) ? data.events : [];
      if (!events.length) return 'СОБЫТИЙ НЕТ.';
      return events.map((event) => {
        const parts = [
          event?.timestamp || '—',
          event?.actor_name || '—',
          event?.message || '—',
          event?.card_short_id || event?.card_id || '—',
        ];
        if (event?.details_text) parts.push(event.details_text);
        return parts.join(' | ');
      }).join('\\n');
    }

    function buildReadableGptWallEvents(data) {
      const events = Array.isArray(data?.events) ? data.events : [];
      if (!events.length) return 'РЎРћР‘Р«РўРР™ РќР•Рў.';
      return events.map((event, index) => {
        const lines = [
          '[event ' + (index + 1) + ']',
          'time: ' + (event?.timestamp || 'вЂ”'),
          'actor: ' + (event?.actor_name || 'вЂ”'),
          'source: ' + (event?.source || 'вЂ”'),
          'action: ' + (event?.action || 'вЂ”'),
          'message: ' + (event?.message || 'вЂ”'),
        ];
        const cardRef = event?.card_short_id || event?.card_id || '';
        if (cardRef) lines.push('card: ' + cardRef);
        if (event?.card_heading) lines.push('heading: ' + event.card_heading);
        if (event?.details_text) lines.push('details: ' + String(event.details_text).replace(/\r?\n/g, ' / '));
        return lines.join('\\n');
      }).join('\\n\\n');
    }

    function gptWallSectionMetaText(view, data) {
      const meta = data?.meta || {};
      const sections = data?.sections || {};
      const section = sections?.[view] || {};
      if (view === 'event_log') {
        const sectionMeta = section?.meta || {};
        return 'ЖУРНАЛ СОБЫТИЙ | СОБРАНО: ' + formatDate(sectionMeta.generated_at || meta.generated_at) +
          ' | ПОКАЗАНО: ' + (sectionMeta.events_returned ?? meta.events_returned ?? 0) +
          ' | ВСЕГО: ' + (sectionMeta.events_total ?? meta.events_total ?? 0);
      }
      return 'СОДЕРЖАНИЕ ДОСКИ | ' + gptWallMetaText(section?.meta || meta);
    }

    function gptWallSectionText(view, data) {
      const sections = data?.sections || {};
      const section = sections?.[view] || {};
      if (view === 'event_log') {
        return section?.text || buildReadableGptWallEvents(data);
      }
      return section?.text || data?.text || 'ДАННЫХ НЕТ.';
    }

    function renderGptWallView() {
      const view = normalizeGptWallView(state.gptWallView);
      if (els.gptWallBoardTab) els.gptWallBoardTab.classList.toggle('is-active', view === 'board_content');
      if (els.gptWallEventsTab) els.gptWallEventsTab.classList.toggle('is-active', view === 'event_log');
      els.gptWallMeta.textContent = gptWallSectionMetaText(view, state.gptWall);
      els.gptWallText.dataset.wallView = view;
      els.gptWallText.textContent = gptWallSectionText(view, state.gptWall);
    }

    function setGptWallView(view) {
      state.gptWallView = normalizeGptWallView(view);
      renderGptWallView();
    }

    function setModalListError(metaEl, listEl, metaText, bodyText) {
      metaEl.textContent = metaText;
      listEl.innerHTML = '<div class="log-row__meta">' + escapeHtml(bodyText) + '</div>';
    }

    function setModalTextError(metaEl, textEl, metaText, bodyText) {
      metaEl.textContent = metaText;
      textEl.textContent = bodyText;
    }

    renderArchive = function() {
      const cards = state.snapshot?.archive || [];
      els.archiveList.innerHTML = cards.length
        ? renderCompactArchiveRows(cards)
        : '<div class="log-row__meta">АРХИВ ПУСТ.</div>';
    };

    legacyRenderRepairOrdersBase = function(data) {
      const items = data?.repair_orders || [];
      const meta = data?.meta || {};
      els.repairOrdersMeta.textContent = repairOrdersMetaText(items, meta);
      els.repairOrdersList.innerHTML = items.length
        ? renderRepairOrderRows(items)
        : '<div class="log-row__meta">ЗАКАЗ-НАРЯДОВ ПОКА НЕТ.</div>';
    };

    function legacyRepairOrderListTotalTextBase(value) {
      const normalized = String(value ?? '').trim();
      return normalized || '0';
    }

    function legacyRepairOrdersMetaTextShadow(items, meta) {
      return 'ПОКАЗАНО: ' + items.length +
        ' | ВСЕГО: ' + (meta.total ?? items.length) +
        ' | СПИСОК: ДАТА / АВТО / СУТЬ / СУММА';
    }

    function legacyRenderRepairOrderRowsShadow(items) {
      return items.map((item) => {
        const number = item.number || '-';
        const createdAt = formatDate(item.created_at || item.date || item.updated_at);
        const vehicle = item.vehicle || 'Авто не указано';
        const heading = item.heading || 'Заказ-наряд';
        const total = repairOrderListTotalText(item.grand_total);
        return '<div class="archive-row repair-orders-row" role="button" tabindex="0" data-open-repair-order-card="' + escapeHtml(item.card_id) + '" title="Открыть заказ-наряд">'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__label">Открыта</div><div class="repair-orders-row__number">№ ' + escapeHtml(number) + ' | ' + escapeHtml(createdAt) + '</div></div>'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__label">Автомобиль</div><div class="repair-orders-row__vehicle" title="' + escapeHtml(vehicle) + '">' + escapeHtml(vehicle) + '</div></div>'
          + '<div class="repair-orders-row__cell repair-orders-row__title-cell"><div class="repair-orders-row__label">Смысл карточки</div><div class="repair-orders-row__title" title="' + escapeHtml(heading) + '">' + escapeHtml(heading) + '</div></div>'
          + '<div class="repair-orders-row__cell repair-orders-row__total-cell"><div class="repair-orders-row__label">Сумма</div><div class="repair-orders-row__total" data-empty="' + String(total === '0') + '">' + escapeHtml(total) + '</div></div>'
          + '</div>';
      }).join('');
    }

    async function legacyLoadRepairOrdersBase(openModal = false) {
      try {
        const data = await api('/api/list_repair_orders?limit=300');
        renderRepairOrders(data);
        if (openModal) els.repairOrdersModal.classList.add('is-open');
      } catch (error) {
        els.repairOrdersMeta.textContent = 'ОШИБКА ЗАГРУЗКИ СПИСКА ЗАКАЗ-НАРЯДОВ.';
        els.repairOrdersList.innerHTML = '<div class="log-row__meta">' + escapeHtml(error.message) + '</div>';
        if (openModal) els.repairOrdersModal.classList.add('is-open');
        setStatus(error.message, true);
      }
    }

    async function openCardWorkspace(cardId, { closeModalEl = null, openRepairOrder = false } = {}) {
      const normalizedCardId = String(cardId || '').trim();
      if (!normalizedCardId) return null;
      const data = await api('/api/open_card', { method: 'POST', body: { card_id: normalizedCardId } });
      if (closeModalEl) closeModalEl.classList.remove('is-open');
      openCardModal(data.card);
      if (openRepairOrder) openRepairOrderModal();
      return data.card;
    }

    async function openRepairOrderCard(cardId) {
      try {
        await openCardWorkspace(cardId, { closeModalEl: els.repairOrdersModal, openRepairOrder: true });
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    function updateRepairOrdersTabs() {
      const isClosed = state.repairOrdersFilter === 'closed';
      if (els.repairOrdersOpenTab) els.repairOrdersOpenTab.classList.toggle('is-active', !isClosed);
      if (els.repairOrdersClosedTab) els.repairOrdersClosedTab.classList.toggle('is-active', isClosed);
    }

    function normalizeRepairOrdersSortBy(value) {
      const normalized = String(value || '').trim().toLowerCase();
      return REPAIR_ORDER_SORT_FIELDS.includes(normalized) ? normalized : 'opened_at';
    }

    function normalizeRepairOrdersSortDir(value) {
      const normalized = String(value || '').trim().toLowerCase();
      return REPAIR_ORDER_SORT_DIRECTIONS.includes(normalized) ? normalized : 'desc';
    }

    function syncRepairOrdersControls() {
      if (els.repairOrdersSearchInput) els.repairOrdersSearchInput.value = state.repairOrdersQuery;
      if (els.repairOrdersSortBy) els.repairOrdersSortBy.value = normalizeRepairOrdersSortBy(state.repairOrdersSortBy);
      if (els.repairOrdersSortDir) els.repairOrdersSortDir.value = normalizeRepairOrdersSortDir(state.repairOrdersSortDir);
    }

    function repairOrdersRequestPath() {
      const params = new URLSearchParams();
      params.set('limit', '300');
      params.set('status', state.repairOrdersFilter === 'closed' ? 'closed' : 'open');
      params.set('sort_by', normalizeRepairOrdersSortBy(state.repairOrdersSortBy));
      params.set('sort_dir', normalizeRepairOrdersSortDir(state.repairOrdersSortDir));
      if (state.repairOrdersQuery) params.set('query', state.repairOrdersQuery);
      return '/api/list_repair_orders?' + params.toString();
    }

    legacyRepairOrderListTotalTextShadow = function(value, fallbackValue = '') {
      const normalized = String(value ?? '').trim();
      if (normalized && normalized !== '0') return normalized;
      const fallback = String(fallbackValue ?? '').trim();
      return fallback || normalized || '0';
    };

    legacyRepairOrdersMetaTextShadow2 = function(items, meta) {
      return 'ПОКАЗАНО: ' + items.length +
        ' | ОТКРЫТЫЕ: ' + (meta.active_total ?? 0) +
        ' | АРХИВ: ' + (meta.archived_total ?? 0);
    };

    function legacyRenderRepairOrderListRowsShadow2(items) {
      return items.map((item) => {
        const number = item.number || '-';
        const openedAt = repairOrderDateDisplayValue(item.opened_at || item.created_at || item.date || item.updated_at);
        const vehicle = item.vehicle || 'Авто не указано';
        const client = item.client || 'Клиент не указан';
        const phone = item.phone || 'Телефон не указан';
        const heading = item.summary || item.reason || item.heading || 'Заказ-наряд';
        const total = repairOrderListTotalText(item.grand_total, item.works_total);
        const status = item.status_label || repairOrderStatusLabel(item.status);
        const rawStatus = String(item.status || 'open').trim().toLowerCase() === 'closed' ? 'closed' : 'open';
        return '<div class="archive-row repair-orders-row" role="button" tabindex="0" data-open-repair-order-card="' + escapeHtml(item.card_id) + '" title="Открыть заказ-наряд">'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__label">Номер / открыта</div><div class="repair-orders-row__number">№ ' + escapeHtml(number) + ' | ' + escapeHtml(openedAt) + '</div></div>'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__label">Статус</div><div class="repair-orders-row__status" data-status="' + escapeHtml(rawStatus) + '">' + escapeHtml(status) + '</div></div>'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__label">Владелец</div><div class="repair-orders-row__client" title="' + escapeHtml(client) + '">' + escapeHtml(client) + '</div></div>'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__label">Телефон</div><div class="repair-orders-row__phone" title="' + escapeHtml(phone) + '">' + escapeHtml(phone) + '</div></div>'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__label">Автомобиль</div><div class="repair-orders-row__vehicle" title="' + escapeHtml(vehicle) + '">' + escapeHtml(vehicle) + '</div></div>'
          + '<div class="repair-orders-row__cell repair-orders-row__title-cell"><div class="repair-orders-row__label">Смысл карточки</div><div class="repair-orders-row__title" title="' + escapeHtml(heading) + '">' + escapeHtml(heading) + '</div></div>'
          + '<div class="repair-orders-row__cell repair-orders-row__total-cell"><div class="repair-orders-row__label">Сумма</div><div class="repair-orders-row__total" data-empty="' + String(total === '0') + '">' + escapeHtml(total) + '</div></div>'
          + '</div>';
      }).join('');
    }

    legacyRenderRepairOrdersShadow2 = function(data) {
      const items = data?.repair_orders || [];
      const meta = data?.meta || {};
      if (meta.status === 'open' || meta.status === 'closed') state.repairOrdersFilter = meta.status;
      updateRepairOrdersTabs();
      els.repairOrdersMeta.textContent = repairOrdersMetaText(items, meta);
      els.repairOrdersList.innerHTML = items.length
        ? renderRepairOrderListRows(items)
        : '<div class="log-row__meta">' + (state.repairOrdersFilter === 'closed' ? 'АРХИВ ЗАКАЗ-НАРЯДОВ ПУСТ.' : 'ОТКРЫТЫХ ЗАКАЗ-НАРЯДОВ ПОКА НЕТ.') + '</div>';
    };

    async function setRepairOrdersFilter(status, { openModal = false } = {}) {
      state.repairOrdersFilter = String(status || '').trim().toLowerCase() === 'closed' ? 'closed' : 'open';
      updateRepairOrdersTabs();
      await loadRepairOrders(openModal);
    }

    legacyLoadRepairOrdersFilteredShadow = async function(openModal = false) {
      const filter = state.repairOrdersFilter === 'closed' ? 'closed' : 'open';
      await loadModalData('/api/list_repair_orders?limit=300&status=' + encodeURIComponent(filter), {
        openModal,
        modalEl: els.repairOrdersModal,
        onSuccess: renderRepairOrders,
        onError: (error) => {
          setModalListError(
            els.repairOrdersMeta,
            els.repairOrdersList,
            'ОШИБКА ЗАГРУЗКИ СПИСКА ЗАКАЗ-НАРЯДОВ.',
            error.message,
          );
        },
      });
    };

    renderRepairOrderRows = function(section, rows) {
      const body = repairOrderRowsBody(section);
      body.innerHTML = ensureRepairOrderRows(rows).map((row, index) => repairOrderRowHtml(section, row, index)).join('');
      syncRepairOrderTotals();
    };

    async function handleRepairOrdersListKeydown(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const row = target.closest('[data-open-repair-order-card]');
      if (!row) return;
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      await openRepairOrderCard(row.dataset.openRepairOrderCard);
    }

    function renderGptWall(data) {
      state.gptWall = data;
      renderGptWallView();
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

    legacyLoadRepairOrdersShadow = async function(openModal = false) {
      await loadModalData('/api/list_repair_orders?limit=300', {
        openModal,
        modalEl: els.repairOrdersModal,
        onSuccess: renderRepairOrders,
        onError: (error) => {
          setModalListError(
            els.repairOrdersMeta,
            els.repairOrdersList,
            'ОШИБКА ЗАГРУЗКИ СПИСКА ЗАКАЗ-НАРЯДОВ.',
            error.message,
          );
        },
      });
    };

    repairOrderListTotalText = function(value, fallbackValue = '') {
      const normalized = String(value ?? '').trim();
      if (normalized && normalized !== '0') return normalized;
      const fallback = String(fallbackValue ?? '').trim();
      return fallback || normalized || '0';
    };

    // РЎРџРРЎРћРљ: Р”РђРўРђ / РђР’РўРћ / РЎРЈРўР¬ / РЎРЈРњРњРђ
    repairOrdersMetaText = function(items, meta) {
      const parts = [
        'ПОКАЗАНО: ' + items.length,
        'ОТКРЫТЫЕ: ' + (meta.active_total ?? 0),
        'АРХИВ: ' + (meta.archived_total ?? 0),
      ];
      if (meta.query) parts.push('ПОИСК: ' + String(meta.query).trim());
      const sortBy = normalizeRepairOrdersSortBy(meta.sort_by || state.repairOrdersSortBy);
      const sortDir = normalizeRepairOrdersSortDir(meta.sort_dir || state.repairOrdersSortDir);
      const sortLabel = sortBy === 'number' ? 'НОМЕР' : (sortBy === 'closed_at' ? 'ДАТА ЗАКРЫТИЯ' : 'ДАТА ОТКРЫТИЯ');
      parts.push('СОРТ: ' + sortLabel + ' ' + (sortDir === 'asc' ? '↑' : '↓'));
      return parts.join(' | ');
    };

    legacyRenderRepairOrderListRowsShadow3 = function(items) {
      return items.map((item) => {
        const number = item.number || '-';
        const openedAt = repairOrderDateDisplayValue(item.opened_at || item.created_at || item.date || item.updated_at);
        const closedAt = repairOrderDateDisplayValue(item.closed_at);
        const vehicle = item.vehicle || 'Авто не указано';
        const client = item.client || 'Клиент не указан';
        const phone = item.phone || 'Телефон не указан';
        const heading = item.summary || item.reason || item.heading || 'Заказ-наряд';
        const total = repairOrderListTotalText(item.grand_total, item.works_total);
        const status = item.status_label || repairOrderStatusLabel(item.status);
        const rawStatus = String(item.status || 'open').trim().toLowerCase() === 'closed' ? 'closed' : 'open';
        const allTags = normalizeRepairOrderTags(item.tags || []);
        const previewTags = allTags.slice(0, 3);
        const extraTags = allTags.length - previewTags.length;
        const tagsHtml = previewTags.length
          ? '<div class="repair-orders-row__tags">' + previewTags.map((tag) => '<span class="tag" data-tag-color="' + escapeHtml(tag.color) + '"><span class="tag__dot"></span>' + escapeHtml(tag.label) + '</span>').join('') + (extraTags > 0 ? '<span class="tag">+' + extraTags + '</span>' : '') + '</div>'
          : '';
        return '<div class="archive-row repair-orders-row" role="button" tabindex="0" data-open-repair-order-card="' + escapeHtml(item.card_id) + '" title="Открыть заказ-наряд">'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__label">Номер</div><div class="repair-orders-row__number">№ ' + escapeHtml(number) + '</div></div>'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__label">Открыта</div><div class="repair-orders-row__opened">' + escapeHtml(openedAt || '—') + '</div></div>'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__label">Закрыта</div><div class="repair-orders-row__closed">' + escapeHtml(closedAt || '—') + '</div></div>'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__label">Статус</div><div class="repair-orders-row__status" data-status="' + escapeHtml(rawStatus) + '">' + escapeHtml(status) + '</div></div>'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__label">Владелец</div><div class="repair-orders-row__client" title="' + escapeHtml(client) + '">' + escapeHtml(client) + '</div></div>'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__label">Телефон</div><div class="repair-orders-row__phone" title="' + escapeHtml(phone) + '">' + escapeHtml(phone) + '</div></div>'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__label">Автомобиль</div><div class="repair-orders-row__vehicle" title="' + escapeHtml(vehicle) + '">' + escapeHtml(vehicle) + '</div></div>'
          + '<div class="repair-orders-row__cell repair-orders-row__title-cell"><div class="repair-orders-row__label">Смысл карточки</div><div class="repair-orders-row__title" title="' + escapeHtml(heading) + '">' + escapeHtml(heading) + '</div>' + tagsHtml + '</div>'
          + '<div class="repair-orders-row__cell repair-orders-row__total-cell"><div class="repair-orders-row__label">Сумма</div><div class="repair-orders-row__total" data-empty="' + String(total === '0') + '">' + escapeHtml(total) + '</div></div>'
          + '</div>';
      }).join('');
    };

    legacyRenderRepairOrderListRowsShadow4 = function(items) {
      return items.map((item) => {
        const number = item.number || '-';
        const openedAt = repairOrderListDateDisplayValue(item.opened_at || item.created_at || item.date || item.updated_at);
        const closedAt = repairOrderListDateDisplayValue(item.closed_at);
        const vehicle = String(item.vehicle || '').trim() || '—';
        const client = String(item.client || '').trim();
        const phone = String(item.phone || '').trim();
        const clientText = [client, phone].filter(Boolean).join(' · ') || '—';
        const heading = item.summary || item.reason || item.heading || 'Заказ-наряд';
        const total = repairOrderListTotalText(item.grand_total, item.works_total);
        const status = item.status_label || repairOrderStatusLabel(item.status);
        const rawStatus = String(item.status || 'open').trim().toLowerCase() === 'closed' ? 'closed' : 'open';
        const allTags = normalizeRepairOrderTags(item.tags || []);
        const previewTags = allTags.slice(0, 3);
        const extraTags = allTags.length - previewTags.length;
        const tagsHtml = previewTags.length
          ? '<div class="repair-orders-row__tags">' + previewTags.map((tag) => '<span class="tag" data-tag-color="' + escapeHtml(tag.color) + '"><span class="tag__dot"></span>' + escapeHtml(tag.label) + '</span>').join('') + (extraTags > 0 ? '<span class="tag">+' + extraTags + '</span>' : '') + '</div>'
          : '';
        return '<div class="archive-row repair-orders-row" role="button" tabindex="0" data-open-repair-order-card="' + escapeHtml(item.card_id) + '" title="Открыть заказ-наряд">'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__number">№ ' + escapeHtml(number) + '</div></div>'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__opened">' + escapeHtml(openedAt || '—') + '</div></div>'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__closed">' + escapeHtml(closedAt || '—') + '</div></div>'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__status" data-status="' + escapeHtml(rawStatus) + '">' + escapeHtml(status) + '</div></div>'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__client" title="' + escapeHtml(clientText) + '">' + escapeHtml(clientText) + '</div></div>'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__vehicle" title="' + escapeHtml(vehicle) + '">' + escapeHtml(vehicle) + '</div></div>'
          + '<div class="repair-orders-row__cell repair-orders-row__title-cell"><div class="repair-orders-row__title" title="' + escapeHtml(heading) + '">' + escapeHtml(heading) + '</div>' + tagsHtml + '</div>'
          + '<div class="repair-orders-row__cell repair-orders-row__total-cell"><div class="repair-orders-row__total" data-empty="' + String(total === '0') + '">' + escapeHtml(total) + '</div></div>'
          + '</div>';
      }).join('');
    };

    renderRepairOrderListRows = function(items) {
      return items.map((item) => {
        const number = item.number || '-';
        const openedAt = repairOrderListDateDisplayValue(item.opened_at || item.created_at || item.date || item.updated_at);
        const closedAt = repairOrderListDateDisplayValue(item.closed_at);
        const vehicle = String(item.vehicle || '').trim() || '-';
        const client = String(item.client || '').trim();
        const phone = String(item.phone || '').trim();
        const clientText = [client, phone].filter(Boolean).join(' / ') || '-';
        const heading = item.summary || item.reason || item.heading || '-';
        const total = repairOrderListTotalText(item.grand_total, item.works_total);
        const status = item.status_label || repairOrderStatusLabel(item.status);
        const rawStatus = String(item.status || 'open').trim().toLowerCase() === 'closed' ? 'closed' : 'open';
        const allTags = normalizeRepairOrderTags(item.tags || []);
        const previewTags = allTags.slice(0, 3);
        const extraTags = allTags.length - previewTags.length;
        const tagsHtml = previewTags.length
          ? '<div class="repair-orders-row__tags">' + previewTags.map((tag) => '<span class="tag" data-tag-color="' + escapeHtml(tag.color) + '"><span class="tag__dot"></span>' + escapeHtml(tag.label) + '</span>').join('') + (extraTags > 0 ? '<span class="tag">+' + extraTags + '</span>' : '') + '</div>'
          : '';
        return '<div class="archive-row repair-orders-row" role="button" tabindex="0" data-open-repair-order-card="' + escapeHtml(item.card_id) + '" title="Open repair order">'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__number">№ ' + escapeHtml(number) + '</div></div>'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__opened">' + escapeHtml(openedAt || '-') + '</div></div>'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__closed">' + escapeHtml(closedAt || '-') + '</div></div>'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__status" data-status="' + escapeHtml(rawStatus) + '">' + escapeHtml(status) + '</div></div>'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__client" title="' + escapeHtml(clientText) + '">' + escapeHtml(clientText) + '</div></div>'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__vehicle" title="' + escapeHtml(vehicle) + '">' + escapeHtml(vehicle) + '</div></div>'
          + '<div class="repair-orders-row__cell repair-orders-row__title-cell"><div class="repair-orders-row__title" title="' + escapeHtml(heading) + '">' + escapeHtml(heading) + '</div>' + tagsHtml + '</div>'
          + '<div class="repair-orders-row__cell repair-orders-row__total-cell"><div class="repair-orders-row__total" data-empty="' + String(total === '0') + '">' + escapeHtml(total) + '</div></div>'
          + '</div>';
      }).join('');
    };

    renderRepairOrders = function(data) {
      const items = data?.repair_orders || [];
      const meta = data?.meta || {};
      if (meta.status === 'open' || meta.status === 'closed') state.repairOrdersFilter = meta.status;
      state.repairOrdersQuery = String(meta.query ?? state.repairOrdersQuery ?? '').trim();
      state.repairOrdersSortBy = normalizeRepairOrdersSortBy(meta.sort_by || state.repairOrdersSortBy);
      state.repairOrdersSortDir = normalizeRepairOrdersSortDir(meta.sort_dir || state.repairOrdersSortDir);
      updateRepairOrdersTabs();
      syncRepairOrdersControls();
      els.repairOrdersMeta.textContent = repairOrdersMetaText(items, meta);
      els.repairOrdersList.innerHTML = items.length
        ? renderRepairOrderListRows(items)
        : '<div class="log-row__meta">' + (state.repairOrdersQuery
            ? 'ПО ПОИСКУ НИЧЕГО НЕ НАЙДЕНО.'
            : (state.repairOrdersFilter === 'closed' ? 'АРХИВ ЗАКАЗ-НАРЯДОВ ПУСТ.' : 'ОТКРЫТЫХ ЗАКАЗ-НАРЯДОВ ПОКА НЕТ.')) + '</div>';
    };

    loadRepairOrders = async function(openModal = false) {
      await loadModalData(repairOrdersRequestPath(), {
        openModal,
        modalEl: els.repairOrdersModal,
        onSuccess: renderRepairOrders,
        onError: (error) => {
          setModalListError(
            els.repairOrdersMeta,
            els.repairOrdersList,
            'ОШИБКА ЗАГРУЗКИ СПИСКА ЗАКАЗ-НАРЯДОВ.',
            error.message,
          );
        },
      });
    };

    function handleRepairOrdersSearchInput() {
      state.repairOrdersQuery = String(els.repairOrdersSearchInput?.value || '').trim();
      if (state.repairOrdersLoadTimer) window.clearTimeout(state.repairOrdersLoadTimer);
      state.repairOrdersLoadTimer = window.setTimeout(() => {
        state.repairOrdersLoadTimer = null;
        loadRepairOrders(false);
      }, 180);
    }

    function handleRepairOrdersSortChange() {
      state.repairOrdersSortBy = normalizeRepairOrdersSortBy(els.repairOrdersSortBy?.value);
      state.repairOrdersSortDir = normalizeRepairOrdersSortDir(els.repairOrdersSortDir?.value);
      loadRepairOrders(false);
    }

    loadGptWall = async function(openModal = false) {
      await loadModalData('/api/get_gpt_wall', {
        method: 'POST',
        body: { include_archived: true, event_limit: 100 },
        openModal,
        modalEl: els.gptWallModal,
        onSuccess: renderGptWall,
        onError: (error) => {
          setModalTextError(
            els.gptWallMeta,
            els.gptWallText,
            'ОШИБКА ЗАГРУЗКИ СЛОЯ GPT.',
            error.message,
          );
        },
      });
    };

    function cardHtml(card) {
      const previewTags = (card.tags || []).slice(0, CARD_TAG_LIMIT);
      const extraTags = (card.tags || []).length - previewTags.length;
      const tagsHtml = previewTags.length
        ? previewTags.map((tag) => '<span class="tag">' + escapeHtml(tag) + '</span>').join('') + (extraTags > 0 ? '<span class="tag">+' + extraTags + '</span>' : '')
        : '<span class="tag tag--muted">БЕЗ МЕТОК</span>';
      const headingHtml = card.vehicle
        ? '<div class="card__heading"><div class="card__vehicle">' + escapeHtml(card.vehicle) + '</div><span class="card__slash">/</span><div class="card__title">' + escapeHtml(card.title) + '</div></div>'
        : '<div class="card__title">' + escapeHtml(card.title) + '</div>';
      return '<article class="card" draggable="true" data-card-id="' + escapeHtml(card.id) + '" data-indicator="' + escapeHtml(card.indicator) + '" data-status="' + escapeHtml(card.status) + '" data-blink="' + (card.is_blinking ? 'true' : 'false') + '">' + headingHtml + '<div class="card__desc">' + escapeHtml(card.description || 'Описание не указано') + '</div><div class="card__signal"><span class="card__signal-label"><span class="lamp" data-indicator="' + escapeHtml(card.indicator) + '"></span><span>СИГН</span></span><span class="card__signal-value">' + durationToMarkup(card.remaining_seconds, false) + '</span></div><div class="card__tags">' + tagsHtml + '</div><div class="meta-line"><span>ФАЙЛЫ ' + escapeHtml(card.attachment_count) + '</span><span>ЖУРНАЛ ' + escapeHtml(card.events_count) + '</span></div></article>';
    }

    function renderCardHtml(card) {
      const normalizedTags = normalizeDraftTags(card.tag_items || card.tags || []);
      const previewTags = normalizedTags.slice(0, CARD_TAG_LIMIT);
      const extraTags = normalizedTags.length - previewTags.length;
      const tagsHtml = previewTags.length
        ? previewTags.map((tag) => '<span class="tag" data-tag-color="' + escapeHtml(tag.color) + '"><span class="tag__dot"></span>' + escapeHtml(tag.label) + '</span>').join('') + (extraTags > 0 ? '<span class="tag">+' + extraTags + '</span>' : '')
        : '<span class="tag tag--muted">БЕЗ МЕТОК</span>';
      const headingHtml = card.vehicle
        ? '<div class="card__heading"><div class="card__vehicle">' + escapeHtml(card.vehicle) + '</div><span class="card__slash">/</span><div class="card__title">' + escapeHtml(card.title) + '</div></div>'
        : '<div class="card__title">' + escapeHtml(card.title) + '</div>';
      const heatStyle = '--deadline-heat-border:' + escapeHtml(card.deadline_heat_border_color || 'rgba(83, 191, 122, 0.34)') + ';--deadline-heat-ring:' + escapeHtml(card.deadline_heat_ring_color || 'rgba(83, 191, 122, 0.08)') + ';--deadline-heat-glow:' + escapeHtml(card.deadline_heat_glow_color || 'rgba(83, 191, 122, 0.04)') + ';';
      return '<article class="card" style="' + heatStyle + '" draggable="true" data-card-id="' + escapeHtml(card.id) + '" data-indicator="' + escapeHtml(card.indicator) + '" data-status="' + escapeHtml(card.status) + '" data-blink="' + (card.is_blinking ? "true" : "false") + '" data-deadline-bucket="' + escapeHtml(card.deadline_progress_bucket ?? 0) + '" data-deadline-step="' + escapeHtml(card.deadline_progress_step_percent ?? 0) + '">' + headingHtml + '<div class="card__desc">' + escapeHtml(card.description || 'Описание не указано') + '</div><div class="card__signal"><span class="card__signal-label"><span class="lamp" data-indicator="' + escapeHtml(card.indicator) + '"></span><span>СИГН</span></span><span class="card__signal-value">' + durationToMarkup(card.remaining_seconds, false) + '</span></div><div class="card__tags">' + tagsHtml + '</div><div class="meta-line"><span>ФАЙЛЫ ' + escapeHtml(card.attachment_count) + '</span><span>ЖУРНАЛ ' + escapeHtml(card.events_count) + '</span></div></article>';
    }

    function renderBoardCardHtml(card) {
      const normalizedTags = normalizeDraftTags(card.tag_items || card.tags || []);
      const previewTags = normalizedTags.slice(0, CARD_TAG_LIMIT);
      const extraTags = normalizedTags.length - previewTags.length;
      const tagsHtml = previewTags.length
        ? previewTags.map((tag) => '<span class="tag" data-tag-color="' + escapeHtml(tag.color) + '"><span class="tag__dot"></span>' + escapeHtml(tag.label) + '</span>').join('') + (extraTags > 0 ? '<span class="tag">+' + extraTags + '</span>' : '')
        : '<span class="tag tag--muted">Р‘Р•Р— РњР•РўРћРљ</span>';
      const headingHtml = buildCardHeadingHtml(card);
      const unreadBadgeHtml = cardUnreadBadgeHtml(card);
      const heatStyle = '--deadline-heat-border:' + escapeHtml(card.deadline_heat_border_color || 'rgba(83, 191, 122, 0.34)') + ';--deadline-heat-ring:' + escapeHtml(card.deadline_heat_ring_color || 'rgba(83, 191, 122, 0.08)') + ';--deadline-heat-glow:' + escapeHtml(card.deadline_heat_glow_color || 'rgba(83, 191, 122, 0.04)') + ';';
      return '<article class="card" style="' + heatStyle + '" draggable="true" data-card-id="' + escapeHtml(card.id) + '" data-indicator="' + escapeHtml(card.indicator) + '" data-status="' + escapeHtml(card.status) + '" data-blink="' + (card.is_blinking ? "true" : "false") + '" data-unread="' + (card.is_unread ? 'true' : 'false') + '" data-deadline-bucket="' + escapeHtml(card.deadline_progress_bucket ?? 0) + '" data-deadline-step="' + escapeHtml(card.deadline_progress_step_percent ?? 0) + '">' + unreadBadgeHtml + headingHtml + '<div class="card__desc">' + escapeHtml(card.description || 'РћРїРёСЃР°РЅРёРµ РЅРµ СѓРєР°Р·Р°РЅРѕ') + '</div><div class="card__signal"><span class="card__signal-label"><span class="lamp" data-indicator="' + escapeHtml(card.indicator) + '"></span><span>РЎРР“Рќ</span></span><span class="card__signal-value">' + durationToMarkup(card.remaining_seconds, false) + '</span></div><div class="card__tags">' + tagsHtml + '</div><div class="meta-line"><span>Р¤РђР™Р›Р« ' + escapeHtml(card.attachment_count) + '</span><span>Р–РЈР РќРђР› ' + escapeHtml(card.events_count) + '</span></div></article>';
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
        const cards = (grouped.get(column.id) || []).slice().sort((left, right) =>
          ((left.position ?? 0) - (right.position ?? 0))
          || String(left.created_at || '').localeCompare(String(right.created_at || ''))
          || String(left.id || '').localeCompare(String(right.id || ''))
        );
        const tone = COLUMN_TONES[index % COLUMN_TONES.length];
        const toneStyle = '--column-tint:' + tone.tint + ';--column-head:' + tone.head + ';--column-edge:' + tone.edge + ';--column-empty:' + tone.empty + ';';
        const isDeleteBlocked = cards.length > 0 || snapshot.columns.length <= 1;
        const deleteTitle = cards.length > 0
          ? 'Сначала убери карточки из этого столбца'
          : (snapshot.columns.length <= 1 ? 'Последний столбец нельзя удалить' : 'Удалить пустой столбец');
        const deleteAttrs = isDeleteBlocked ? ' disabled' : '';
        return '<section class="column" style="' + toneStyle + '" data-column-id="' + escapeHtml(column.id) + '"><div class="column__head"><div class="column__title">' + escapeHtml(column.label) + '</div><div class="column__head-actions"><button class="btn btn--ghost column__delete" type="button" data-delete-column="' + escapeHtml(column.id) + '" data-column-label="' + escapeHtml(column.label) + '" data-card-count="' + cards.length + '" title="' + escapeHtml(deleteTitle) + '" aria-label="' + escapeHtml(deleteTitle) + '"' + deleteAttrs + '>×</button><div class="column__count">' + cards.length + '</div></div></div><div class="column__cards">' + (cards.length ? cards.map(renderBoardCardHtml).join('') : '<div class="empty">ЗДЕСЬ ПОКА ПУСТО.</div>') + '</div><button class="btn" data-create-in="' + escapeHtml(column.id) + '">+ КАРТОЧКА</button></section>';
      }).join('') + '<div class="sticky-layer" id="stickyLayer"></div>';
      els.stickyLayer = document.getElementById('stickyLayer');
      snapshot.columns.forEach((column) => {
        const section = els.board.querySelector('[data-column-id="' + column.id + '"]');
        const actions = section?.querySelector('.column__head-actions');
        if (!actions || actions.querySelector('[data-rename-column]')) return;
        const renameTitle = 'Переименовать столбец';
        const button = document.createElement('button');
        button.className = 'btn btn--ghost column__rename';
        button.type = 'button';
        button.setAttribute('data-rename-column', column.id);
        button.setAttribute('data-column-label', column.label);
        button.setAttribute('title', renameTitle);
        button.setAttribute('aria-label', renameTitle);
        button.innerHTML = '&#9998;';
        actions.insertBefore(button, actions.firstChild);
      });
      renderStickies();
    }

    function setTab(name) {
      state.currentTab = name;
      document.querySelectorAll('[data-tab]').forEach((button) => button.classList.toggle('is-active', button.dataset.tab === name));
      document.querySelectorAll('[data-panel]').forEach((panel) => panel.classList.toggle('hidden', panel.dataset.panel !== name));
    }

    function openCardModal(card) {
      applyCardModalState(card);
      setTab('overview');
      els.cardModal.classList.add('is-open');
      requestAnimationFrame(() => syncCardDescriptionHeight());
      if (card?.id) loadLogs(card.id);
    }

    function closeCardModal() {
      closeRepairOrderModal();
      els.cardModal.classList.remove('is-open');
      resetCardModalState();
    }

    async function refreshSnapshot(showSuccess = false) {
      if (state.refreshInFlight) {
        const pending = state.refreshInFlight;
        await pending;
        if (!showSuccess) return;
      }

      state.refreshInFlight = (async () => {
        try {
          state.snapshot = await api('/api/get_board_snapshot?archive_limit=30&compact=1');
          applyBoardScale(state.snapshot?.settings?.board_scale ?? state.boardScale ?? 1, { syncInput: true });
          renderBoard();
          if (els.archiveModal.classList.contains('is-open')) renderArchive();
          primeBoardViewport();
          if (els.gptWallModal.classList.contains('is-open')) await loadGptWall(false);
          const data = state.snapshot;
        setStatus(showSuccess ? ('ДОСКА ОБНОВЛЕНА · ' + new Date().toLocaleTimeString('ru-RU')) : ('СЕРВЕР АКТИВЕН · КАРТОЧЕК: ' + data.cards.length + ' · АРХИВ: ' + data.archive.length));
        } catch (error) {
          setStatus(error.message, true);
        } finally {
          state.refreshInFlight = null;
        }
      })();

      return state.refreshInFlight;
    }

    function snapshotCardById(cardId) {
      if (!cardId) return null;
      const cards = state.snapshot?.cards || [];
      const archive = state.snapshot?.archive || [];
      return cards.find((card) => card.id === cardId) || archive.find((card) => card.id === cardId) || null;
    }

    function replaceSnapshotCard(nextCard) {
      if (!nextCard?.id) return;
      if (Array.isArray(state.snapshot?.cards)) {
        state.snapshot.cards = state.snapshot.cards.map((card) => card.id === nextCard.id ? nextCard : card);
      }
      if (Array.isArray(state.snapshot?.archive)) {
        state.snapshot.archive = state.snapshot.archive.map((card) => card.id === nextCard.id ? nextCard : card);
      }
      if (state.activeCard?.id === nextCard.id) state.activeCard = nextCard;
      renderBoard();
      if (els.archiveModal.classList.contains('is-open')) renderArchive();
    }

    function clearUnreadHoverTimer(cardId) {
      if (!cardId) return;
      const timerId = state.unreadHoverTimers.get(cardId);
      if (timerId) {
        clearTimeout(timerId);
        state.unreadHoverTimers.delete(cardId);
      }
    }

    async function markCardSeen(cardId) {
      if (!cardId) return;
      clearUnreadHoverTimer(cardId);
      const currentCard = snapshotCardById(cardId) || state.activeCard;
      if (currentCard && !currentCard.is_unread && !currentCard.has_unseen_update) return;
      if (state.unreadSeenInFlight.has(cardId)) return;
      state.unreadSeenInFlight.add(cardId);
      try {
        const data = await api('/api/mark_card_seen', {
          method: 'POST',
          body: { card_id: cardId, actor_name: state.actor, source: 'ui' },
        });
        if (data?.card) replaceSnapshotCard(data.card);
      } catch (_) {
      } finally {
        state.unreadSeenInFlight.delete(cardId);
      }
    }

    function scheduleCardSeen(cardId) {
      if (!cardId) return;
      const currentCard = snapshotCardById(cardId);
      if (!currentCard || (!currentCard.is_unread && !currentCard.has_unseen_update)) return;
      if (state.unreadSeenInFlight.has(cardId)) return;
      clearUnreadHoverTimer(cardId);
      const timerId = setTimeout(() => {
        state.unreadHoverTimers.delete(cardId);
        markCardSeen(cardId);
      }, CARD_UNREAD_HOVER_DELAY_MS);
      state.unreadHoverTimers.set(cardId, timerId);
    }

    function stopSnapshotPolling() {
      if (state.pollHandle) {
        clearInterval(state.pollHandle);
        state.pollHandle = null;
      }
    }

    function startSnapshotPolling() {
      stopSnapshotPolling();
      const interval = document.hidden ? SNAPSHOT_POLL_HIDDEN_INTERVAL_MS : SNAPSHOT_POLL_INTERVAL_MS;
      state.pollHandle = setInterval(() => refreshSnapshot(false), interval);
    }

    async function legacySaveCardShadow() {
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
        await openCardWorkspace(cardId);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    function clearCardDropState() {
      state.boardDropColumnId = '';
      state.boardDropBeforeCardId = '';
      document.querySelectorAll('.column.is-drop-target').forEach((column) => column.classList.remove('is-drop-target'));
      document.querySelectorAll('.card.is-drop-before').forEach((card) => card.classList.remove('is-drop-before'));
    }

    function finishCardDrag() {
      clearCardDropState();
      if (state.boardDragCardId) {
        const dragged = document.querySelector('.card[data-card-id="' + state.boardDragCardId + '"]');
        if (dragged) dragged.classList.remove('is-dragging');
      }
      state.boardDragCardId = '';
    }

    function resolveDropBeforeCardId(column, clientY, draggedCardId) {
      const cards = Array.from(column.querySelectorAll('.card')).filter((card) => card.dataset.cardId !== draggedCardId);
      for (const card of cards) {
        const rect = card.getBoundingClientRect();
        if (clientY < rect.top + (rect.height / 2)) return card.dataset.cardId || '';
      }
      return '';
    }

    function updateCardDropState(column, beforeCardId) {
      clearCardDropState();
      if (!column) return;
      state.boardDropColumnId = column.dataset.columnId || '';
      state.boardDropBeforeCardId = beforeCardId || '';
      column.classList.add('is-drop-target');
      if (!beforeCardId) return;
      const beforeCard = column.querySelector('.card[data-card-id="' + beforeCardId + '"]');
      if (beforeCard) beforeCard.classList.add('is-drop-before');
    }

    async function moveCard(cardId, columnId, beforeCardId = '') {
      try {
        await api('/api/move_card', {
          method: 'POST',
          body: {
            card_id: cardId,
            column: columnId,
            before_card_id: beforeCardId || undefined,
            actor_name: state.actor,
            source: 'ui',
          },
        });
        await refreshSnapshot(true);
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        finishCardDrag();
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

    function openNewCardInColumn(columnId) {
      openCardModal({ column: columnId, tags: [], attachments: [], remaining_seconds: 86400 });
    }

    async function renameColumnFromButton(button) {
      const columnId = button.dataset.renameColumn;
      const columnLabel = button.dataset.columnLabel || columnId || 'column';
      const label = window.prompt('Новое название столбца', columnLabel);
      if (label === null) return;
      if (!label.trim()) {
        setStatus('НУЖНО УКАЗАТЬ НАЗВАНИЕ СТОЛБЦА.', true);
        return;
      }
      try {
        await api('/api/rename_column', {
          method: 'POST',
          body: { column_id: columnId, label, actor_name: state.actor, source: 'ui' },
        });
        await refreshSnapshot(true);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function deleteColumnFromButton(button) {
      const columnId = button.dataset.deleteColumn;
      const columnLabel = button.dataset.columnLabel || columnId || 'столбец';
      const cardsCount = Number(button.dataset.cardCount || '0');
      if (button.hasAttribute('disabled')) {
        if (cardsCount > 0) {
          setStatus('СНАЧАЛА УБЕРИ КАРТОЧКИ ИЗ СТОЛБЦА «' + columnLabel + '».', true);
          return;
        }
        setStatus('ПОСЛЕДНИЙ СТОЛБЕЦ УДАЛЯТЬ НЕЛЬЗЯ.', true);
        return;
      }
      if (!window.confirm('Удалить пустой столбец «' + columnLabel + '»?')) return;
      try {
        await api('/api/delete_column', { method: 'POST', body: { column_id: columnId, actor_name: state.actor, source: 'ui' } });
        await refreshSnapshot(true);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function handleAuxiliaryBoardClick(target, event) {
      if (target === els.stickyDockButton || target.closest('#stickyDockButton')) {
        openStickyModal();
        return true;
      }
      const stickyCloseButton = target.closest('.sticky__close');
      if (stickyCloseButton) {
        await removeSticky(stickyCloseButton.closest('.sticky').dataset.stickyId);
        return true;
      }
      if (target.closest('.sticky')) return true;
      const attachmentLink = target.closest('a[href*="/api/attachment"]');
      if (attachmentLink) {
        event.preventDefault();
        try {
          await downloadAttachment(withAccessToken(attachmentLink.getAttribute('href')));
        } catch (error) {
          setStatus(error.message, true);
        }
        return true;
      }
      if (target.dataset.restoreCard) {
        await restoreCard(target.dataset.restoreCard);
        return true;
      }
      return false;
    }

    async function handleCardWorkspaceClick(target) {
      if (target.dataset.copyVehicleField) {
        await copyVehicleFieldValue(target.dataset.copyVehicleField);
        return true;
      }
      if (target.dataset.editRepairOrderTag) {
        editRepairOrderTag(target.dataset.editRepairOrderTag);
        return true;
      }
      if (target.dataset.removeRepairOrderTag) {
        removeRepairOrderTag(target.dataset.removeRepairOrderTag);
        return true;
      }
      if (target.dataset.repairOrderTagColorChoice) {
        state.repairOrderTagColor = normalizeTagColor(target.dataset.repairOrderTagColorChoice);
        renderRepairOrderTags();
        return true;
      }
      const boardCard = target.closest('.card');
      if (boardCard) {
        await openCardById(boardCard.dataset.cardId);
        return true;
      }
      if (target.dataset.removeTag) {
        state.draftTags = state.draftTags.filter((tag) => tag.label !== target.dataset.removeTag);
        renderColorTags();
        return true;
      }
      if (target.dataset.tagColorChoice) {
        state.draftTagColor = normalizeTagColor(target.dataset.tagColorChoice);
        renderColorTags();
        return true;
      }
      if (target.dataset.suggestTag) {
        addSuggestedTag({ label: target.dataset.suggestTag, color: target.dataset.suggestColor });
        return true;
      }
      if (target.dataset.removeFile && state.editingId) {
        await removeActiveCardAttachment(target.dataset.removeFile);
        return true;
      }
      return false;
    }

    function openBoardSettings() {
      applyBoardScale(state.boardScale || 1, { syncInput: true });
      els.boardSettingsModal.classList.add('is-open');
    }

    function handleBoardScaleInput() {
      zoomBoardTo(Number(els.boardScaleInput.value) / 100);
    }

    async function resetBoardScaleToDefault() {
      els.boardScaleInput.value = '100';
      zoomBoardTo(1, { syncInput: true });
      await saveBoardScale();
    }

    async function createColumnFromTopbar() {
      const label = window.prompt('РќР°Р·РІР°РЅРёРµ СЃС‚РѕР»Р±С†Р°');
      if (!label) return;
      try {
        await api('/api/create_column', { method: 'POST', body: { label, actor_name: state.actor, source: 'ui' } });
        await refreshSnapshot(true);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    function openDefaultNewCard() {
      openCardModal({ column: state.snapshot?.columns?.[0]?.id || 'inbox', tags: [], attachments: [], remaining_seconds: 86400 });
    }

    function openRepairOrdersModal() {
      state.repairOrdersFilter = 'open';
      state.repairOrdersQuery = '';
      state.repairOrdersSortBy = 'opened_at';
      state.repairOrdersSortDir = 'desc';
      updateRepairOrdersTabs();
      syncRepairOrdersControls();
      loadRepairOrders(true);
    }

    function openGptWallModal() {
      loadGptWall(true);
    }

    function refreshGptWallView() {
      loadGptWall(false);
    }

    async function archiveActiveCard() {
      if (!state.editingId) return;
      try {
        await api('/api/archive_card', { method: 'POST', body: { card_id: state.editingId, actor_name: state.actor, source: 'ui' } });
        closeCardModal();
        await refreshSnapshot(true);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function restoreActiveCard() {
      if (!state.editingId) return;
      await restoreCard(state.editingId);
      closeCardModal();
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

    function handleStickyModalOverlayClick(event) {
      if (!(event.target instanceof HTMLElement)) return;
      if (event.target.classList.contains('modal')) closeStickyModal();
    }

    function handleRepairOrderModalOverlayClick(event) {
      if (!(event.target instanceof HTMLElement)) return;
      if (event.target.classList.contains('modal')) closeRepairOrderModal();
    }

    function handleOperatorProfileModalOverlayClick(event) {
      if (!(event.target instanceof HTMLElement)) return;
      if (event.target.classList.contains('modal')) els.operatorProfileModal.classList.remove('is-open');
    }

    function handleOperatorAdminModalOverlayClick(event) {
      if (!(event.target instanceof HTMLElement)) return;
      if (event.target.classList.contains('modal')) els.operatorAdminModal.classList.remove('is-open');
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

    async function persistBoardScaleChange() {
      await saveBoardScale();
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

    async function uploadProvidedFiles(files) {
      const normalizedFiles = Array.from(files || []).filter(Boolean);
      if (!normalizedFiles.length) return;
      if (!requireSavedCardForFiles({ syncDropzone: true })) return;
      try {
        for (const file of normalizedFiles) {
          const buffer = await file.arrayBuffer();
          const base64 = arrayBufferToBase64(buffer);
          await api('/api/add_card_attachment', { method: 'POST', body: { card_id: state.editingId, actor_name: state.actor, source: 'ui', file_name: file.name, mime_type: file.type || 'application/octet-stream', content_base64: base64 } });
        }
        await refreshActiveCardFiles();
        setStatus(normalizedFiles.length > 1 ? 'ФАЙЛЫ ЗАГРУЖЕНЫ.' : 'ФАЙЛ ЗАГРУЖЕН.', false);
        await refreshSnapshot(true);
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        els.fileInput.value = '';
        els.fileDropzone.classList.remove('is-active');
      }
    }

    async function uploadFiles() {
      return uploadProvidedFiles(els.fileInput.files);
    }

    function addRepairOrderRowFromButton(section, event) {
      event.preventDefault();
      event.stopPropagation();
      addRepairOrderRow(section);
    }

    function handleRepairOrderModalInput(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      if (target.closest('tr[data-repair-order-row]')) syncRepairOrderTotals();
    }

    function saveRepairOrderDraft() {
      return saveRepairOrder(false);
    }

    function printRepairOrderDraft() {
      return saveRepairOrder(true);
    }

""",
        PRINTING_WEB_MODULE_SCRIPT,
        """
    function openFilePickerFromDropzone() {
      if (!requireSavedCardForFiles()) return;
      els.fileInput.click();
    }

    function handleFileDropzoneKeydown(event) {
      const isPasteShortcut = (event.ctrlKey || event.metaKey) && String(event.key || '').toLowerCase() === 'v';
      if (isPasteShortcut) return;
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openFilePickerFromDropzone();
        return;
      }
      if (event.key.length === 1 || event.key === 'Backspace' || event.key === 'Delete') {
        event.preventDefault();
      }
    }

    function handleFileDropzoneBeforeInput(event) {
      if (event.inputType && event.inputType.startsWith('insert')) event.preventDefault();
    }

    function handleFileDropzoneInput() {
      els.fileDropzone.textContent = '';
    }

    function handleFileDropzoneDragEnter(event) {
      if (!event.dataTransfer) return;
      event.preventDefault();
      if (!state.editingId) return;
      els.fileDropzone.classList.add('is-active');
    }

    function handleFileDropzoneDragOver(event) {
      if (!event.dataTransfer) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = state.editingId ? 'copy' : 'none';
      if (!state.editingId) return;
      els.fileDropzone.classList.add('is-active');
    }

    function handleFileDropzoneDragLeave(event) {
      if (!(event.target instanceof HTMLElement)) return;
      if (event.target !== els.fileDropzone) return;
      els.fileDropzone.classList.remove('is-active');
    }

    async function handleFileDropzoneDrop(event) {
      if (!event.dataTransfer) return;
      event.preventDefault();
      if (!requireSavedCardForFiles()) {
        els.fileDropzone.classList.remove('is-active');
        return;
      }
      await uploadProvidedFiles(event.dataTransfer.files);
    }

    async function handleFileDropzonePaste(event) {
      event.preventDefault();
      if (!requireSavedCardForFiles()) return;
      const files = collectClipboardAttachmentFiles(event);
      if (!files.length) {
        setStatus('Р’ Р‘РЈР¤Р•Р Р• РќР•Рў Р¤РђР™Р›Рђ РР›Р РўР•РљРЎРўРђ Р”Р›РЇ Р’Р›РћР–Р•РќРРЇ.', true);
        return;
      }
      await uploadProvidedFiles(files);
    }

    function handleCardSeenPointerOver(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const card = target.closest('.card');
      if (!card) return;
      const hasUnreadMarker = card.dataset.unread === 'true';
      const hasUpdatedMarker = card.dataset.updatedUnseen === 'true';
      if (!hasUnreadMarker && !hasUpdatedMarker) return;
      const relatedTarget = event.relatedTarget;
      if (relatedTarget instanceof HTMLElement && card.contains(relatedTarget)) return;
      scheduleCardSeen(card.dataset.cardId);
    }

    function handleCardSeenPointerOut(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const card = target.closest('.card');
      if (!card) return;
      const relatedTarget = event.relatedTarget;
      if (relatedTarget instanceof HTMLElement && card.contains(relatedTarget)) return;
      clearUnreadHoverTimer(card.dataset.cardId);
    }

    function handleBoardCardDragStart(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const card = target.closest('.card');
      if (!card) return;
      state.boardDragCardId = card.dataset.cardId || '';
      card.classList.add('is-dragging');
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', state.boardDragCardId);
      }
    }

    function handleBoardCardDragOver(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const column = target.closest('.column');
      if (!column) return;
      event.preventDefault();
      const draggedCardId = state.boardDragCardId || event.dataTransfer?.getData('text/plain') || '';
      const beforeCardId = resolveDropBeforeCardId(column, event.clientY, draggedCardId);
      updateCardDropState(column, beforeCardId);
    }

    function handleBoardCardDragLeave(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const column = target.closest('.column');
      if (!column) return;
      const relatedTarget = event.relatedTarget;
      if (relatedTarget instanceof HTMLElement && column.contains(relatedTarget)) return;
      clearCardDropState();
    }

    async function handleBoardCardDrop(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const column = target.closest('.column');
      if (!column) return;
      event.preventDefault();
      const cardId = state.boardDragCardId || event.dataTransfer?.getData('text/plain') || '';
      const columnId = state.boardDropColumnId || column.dataset.columnId || '';
      const beforeCardId = state.boardDropBeforeCardId || '';
      if (cardId && columnId) {
        await moveCard(cardId, columnId, beforeCardId);
      } else {
        finishCardDrag();
      }
    }

    document.addEventListener('click', async (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      if (target.dataset.close) closeNamedModal(target.dataset.close);
      if (target.dataset.tab) setTab(target.dataset.tab);
      const openRepairOrderModalTarget = target.closest('[data-open-repair-order-modal]');
      if (openRepairOrderModalTarget) {
        event.preventDefault();
        event.stopPropagation();
        openRepairOrderModal();
        return;
      }
      const openRepairOrderCardTarget = target.closest('[data-open-repair-order-card]');
      if (openRepairOrderCardTarget) {
        await openRepairOrderCard(openRepairOrderCardTarget.dataset.openRepairOrderCard);
        return;
      }
      const addRepairOrderRowButton = target.closest('[data-add-repair-order-row]');
      if (addRepairOrderRowButton) {
        addRepairOrderRow(addRepairOrderRowButton.dataset.addRepairOrderRow);
        return;
      }
      const removeRepairOrderRowButton = target.closest('[data-remove-repair-order-row]');
      if (removeRepairOrderRowButton) {
        removeRepairOrderRow(
          removeRepairOrderRowButton.dataset.removeRepairOrderRow,
          Number(removeRepairOrderRowButton.dataset.rowIndex || '-1')
        );
        return;
      }
      const renameColumnButton = target.closest('[data-rename-column]');
      if (renameColumnButton) {
        await renameColumnFromButton(renameColumnButton);
        return;
      }
      const deleteColumnButton = target.closest('[data-delete-column]');
      if (deleteColumnButton) {
        await deleteColumnFromButton(deleteColumnButton);
        return;
      }
      if (target.dataset.createIn) openNewCardInColumn(target.dataset.createIn);
      if (await handleAuxiliaryBoardClick(target, event)) return;
      if (await handleCardWorkspaceClick(target)) return;
    });

    document.addEventListener('pointerover', handleCardSeenPointerOver);
    document.addEventListener('pointerout', handleCardSeenPointerOut);
    document.addEventListener('dragstart', handleBoardCardDragStart);
    document.addEventListener('dragover', handleBoardCardDragOver);
    document.addEventListener('dragleave', handleBoardCardDragLeave);
    document.addEventListener('drop', handleBoardCardDrop);
    document.addEventListener('dragend', finishCardDrag);
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

    /* Legacy pre-session operator listeners removed.
      const actor = els.identityInput.value.trim().toUpperCase();
      if (!actor) return setStatus('НУЖНО УКАЗАТЬ ИМЯ ОПЕРАТОРА.', true);
      state.actor = actor;
      sessionStorage.setItem('legacy-operator-unused', actor);
      ensureActor();
    });
    */
    function remountElement(key) {
      const element = els[key];
      if (!element) return null;
      const clone = element.cloneNode(true);
      element.replaceWith(clone);
      els[key] = clone;
      return clone;
    }

    remountElement('identitySave');
    remountElement('operatorButton');
    remountElement('operatorLogoutButton');
    remountElement('operatorAdminButton');
    remountElement('adminSaveUserButton');
    configureOperatorIdentityUi();

    els.identitySave.addEventListener('click', loginOperator);
    els.identityInput.addEventListener('keydown', handleIdentityInputKeydown);
    if (els.identityPassword) {
      els.identityPassword.addEventListener('keydown', handleIdentityPasswordKeydown);
    }
    els.operatorButton.addEventListener('click', openOperatorWorkspace);
    els.operatorLogoutButton.addEventListener('click', logoutOperator);
    els.operatorAdminButton.addEventListener('click', openOperatorAdminModal);
    els.adminSaveUserButton.addEventListener('click', saveOperatorUser);
    els.adminUsersList.addEventListener('click', handleAdminUsersListClick);

    els.boardSettingsButton.addEventListener('click', openBoardSettings);
    els.archiveButton.addEventListener('click', openArchiveModal);
    els.repairOrdersButton.addEventListener('click', openRepairOrdersModal);
    els.repairOrdersOpenTab.addEventListener('click', () => setRepairOrdersFilter('open'));
    els.repairOrdersClosedTab.addEventListener('click', () => setRepairOrdersFilter('closed'));
    els.repairOrdersSearchInput.addEventListener('input', handleRepairOrdersSearchInput);
    els.repairOrdersSortBy.addEventListener('change', handleRepairOrdersSortChange);
    els.repairOrdersSortDir.addEventListener('change', handleRepairOrdersSortChange);
    els.gptWallButton.addEventListener('click', openGptWallModal);
    els.gptWallBoardTab.addEventListener('click', () => setGptWallView('board_content'));
    els.gptWallEventsTab.addEventListener('click', () => setGptWallView('event_log'));
    els.gptWallRefresh.addEventListener('click', refreshGptWallView);
    els.boardScaleInput.addEventListener('input', handleBoardScaleInput);
    els.boardScaleInput.addEventListener('change', persistBoardScaleChange);
    els.boardScaleReset.addEventListener('click', resetBoardScaleToDefault);
    els.columnButton.addEventListener('click', createColumnFromTopbar);
    els.cardButton.addEventListener('click', openDefaultNewCard);
    [els.signalDays, els.signalHours].forEach((input) => {
      input.addEventListener('input', renderSignalPreview);
      input.addEventListener('change', renderSignalPreview);
    });
    els.tagAddButton.addEventListener('click', addDraftTag);
    els.tagInput.addEventListener('keydown', handleTagInputKeydown);
    configureVehicleAutofillUi();
    els.cardDescription.addEventListener('input', syncCardDescriptionHeight);
    els.vehicleAutofillButton.addEventListener('click', autofillVehicleProfile);
    els.repairOrderAddWorkRowButton.addEventListener('click', (event) => addRepairOrderRowFromButton('works', event));
    els.repairOrderAddMaterialRowButton.addEventListener('click', (event) => addRepairOrderRowFromButton('materials', event));
    els.repairOrderModal.addEventListener('input', handleRepairOrderModalInput);
    els.repairOrderTagAddButton.addEventListener('click', addRepairOrderTag);
    els.repairOrderTagInput.addEventListener('keydown', handleRepairOrderTagInputKeydown);
    els.repairOrderButton.addEventListener('click', openRepairOrderModal);
    els.repairOrderAutofillButton.addEventListener('click', autofillRepairOrder);
    els.repairOrderCloseButton.addEventListener('click', toggleRepairOrderStatus);
    els.repairOrderSaveButton.addEventListener('click', saveRepairOrderDraft);
    els.repairOrderPrintButton.addEventListener('click', printRepairOrderDraft);
    els.saveCardButton.addEventListener('click', saveCard);
    els.saveStickyButton.addEventListener('click', saveSticky);
    els.archiveAction.addEventListener('click', archiveActiveCard);
    els.restoreAction.addEventListener('click', restoreActiveCard);
    els.uploadButton.addEventListener('click', uploadFiles);
    els.fileInput.addEventListener('change', () => uploadProvidedFiles(els.fileInput.files));
    els.fileDropzone.addEventListener('click', openFilePickerFromDropzone);
    els.fileDropzone.addEventListener('keydown', handleFileDropzoneKeydown);
    els.fileDropzone.addEventListener('beforeinput', handleFileDropzoneBeforeInput);
    els.fileDropzone.addEventListener('input', handleFileDropzoneInput);
    els.fileDropzone.addEventListener('dragenter', handleFileDropzoneDragEnter);
    els.fileDropzone.addEventListener('dragover', handleFileDropzoneDragOver);
    els.fileDropzone.addEventListener('dragleave', handleFileDropzoneDragLeave);
    els.fileDropzone.addEventListener('drop', handleFileDropzoneDrop);
    els.fileDropzone.addEventListener('paste', handleFileDropzonePaste);
    els.repairOrdersList.addEventListener('keydown', handleRepairOrdersListKeydown);
    els.stickyModal.addEventListener('click', handleStickyModalOverlayClick);
    els.repairOrderModal.addEventListener('click', handleRepairOrderModalOverlayClick);
    els.operatorProfileModal.addEventListener('click', handleOperatorProfileModalOverlayClick);
    els.operatorAdminModal.addEventListener('click', handleOperatorAdminModalOverlayClick);

    const CARD_VEHICLE_FIELD_LABEL = 'Марка / модель';
    const CARD_TITLE_FIELD_LABEL = 'Краткая суть';
    const CARD_TITLE_REQUIRED_MESSAGE = 'УКАЖИ КРАТКУЮ СУТЬ КАРТОЧКИ.';

    function configureCardFieldSemantics() {
      const vehicleLabel = document.querySelector('label[for="cardVehicle"]');
      if (vehicleLabel) vehicleLabel.textContent = 'МАРКА / МОДЕЛЬ';
      if (els.cardVehicle) {
        els.cardVehicle.placeholder = 'Nissan Teana J32';
        els.cardVehicle.title = 'Указывай только марку и модель автомобиля.';
      }
      const titleLabel = document.querySelector('label[for="cardTitle"]');
      if (titleLabel) titleLabel.textContent = 'КРАТКАЯ СУТЬ';
      if (els.cardTitle) {
        els.cardTitle.placeholder = 'Краткая суть проблемы, задачи или результата';
        els.cardTitle.title = 'Указывай только краткую суть карточки, без марки и модели.';
      }
    }

    function buildVehicleAutofillRawText() {
      const parts = [];
      const vehicle = String(els.cardVehicle.value || '').trim();
      const title = String(els.cardTitle.value || '').trim();
      const description = String(els.cardDescription.value || '').trim();
      if (vehicle) parts.push(CARD_VEHICLE_FIELD_LABEL + ': ' + vehicle);
      if (title) parts.push(CARD_TITLE_FIELD_LABEL + ': ' + title);
      if (description) parts.push('Описание:\\n' + description);
      return parts.join('\\n\\n').trim();
    }

    function buildCardHeadingHtml(card) {
      const vehicle = String(card?.vehicle || '').trim();
      const title = String(card?.title || '').trim();
      if (vehicle && title) {
        return '<div class="card__heading"><div class="card__vehicle">' + escapeHtml(vehicle) + '</div><div class="card__title">' + escapeHtml(title) + '</div></div>';
      }
      if (vehicle) return '<div class="card__vehicle">' + escapeHtml(vehicle) + '</div>';
      return '<div class="card__title">' + escapeHtml(title) + '</div>';
    }

    function cardUnreadBadgeHtml(card) {
      return card?.is_unread
        ? '<div class="card__unread-badge" title="Не прочитано" aria-label="Не прочитано">NEW</div>'
        : '';
    }

    cardUnreadBadgeHtml = function(card) {
      if (card?.is_unread) {
        return '<div class="card__unread-badge" title="Не прочитано" aria-label="Не прочитано">NEW</div>';
      }
      if (card?.has_unseen_update) {
        return '<div class="card__updated-badge" title="Обновлено" aria-label="Обновлено">ОБНОВЛЕНО</div>';
      }
      return '';
    };

    renderBoardCardHtml = function(card) {
      const normalizedTags = normalizeDraftTags(card.tag_items || card.tags || []);
      const previewTags = normalizedTags.slice(0, CARD_TAG_LIMIT);
      const extraTags = normalizedTags.length - previewTags.length;
      const tagsHtml = previewTags.length
        ? previewTags.map((tag) => '<span class="tag" data-tag-color="' + escapeHtml(tag.color) + '"><span class="tag__dot"></span>' + escapeHtml(tag.label) + '</span>').join('') + (extraTags > 0 ? '<span class="tag">+' + extraTags + '</span>' : '')
        : '<span class="tag tag--muted">БЕЗ МЕТОК</span>';
      const headingHtml = buildCardHeadingHtml(card);
      const badgeHtml = cardUnreadBadgeHtml(card);
      const heatStyle = '--deadline-heat-border:' + escapeHtml(card.deadline_heat_border_color || 'rgba(83, 191, 122, 0.34)') + ';--deadline-heat-ring:' + escapeHtml(card.deadline_heat_ring_color || 'rgba(83, 191, 122, 0.08)') + ';--deadline-heat-glow:' + escapeHtml(card.deadline_heat_glow_color || 'rgba(83, 191, 122, 0.04)') + ';';
      return '<article class="card" style="' + heatStyle + '" draggable="true" data-card-id="' + escapeHtml(card.id) + '" data-indicator="' + escapeHtml(card.indicator) + '" data-status="' + escapeHtml(card.status) + '" data-blink="' + (card.is_blinking ? "true" : "false") + '" data-unread="' + (card.is_unread ? 'true' : 'false') + '" data-updated-unseen="' + (card.has_unseen_update ? 'true' : 'false') + '" data-deadline-bucket="' + escapeHtml(card.deadline_progress_bucket ?? 0) + '" data-deadline-step="' + escapeHtml(card.deadline_progress_step_percent ?? 0) + '">' + badgeHtml + headingHtml + '<div class="card__desc">' + escapeHtml(card.description || 'Описание не указано') + '</div><div class="card__signal"><span class="card__signal-label"><span class="lamp" data-indicator="' + escapeHtml(card.indicator) + '"></span><span>СИГН</span></span><span class="card__signal-value">' + durationToMarkup(card.remaining_seconds, false) + '</span></div><div class="card__tags">' + tagsHtml + '</div><div class="meta-line"><span>ФАЙЛЫ ' + escapeHtml(card.attachment_count) + '</span><span>ЖУРНАЛ ' + escapeHtml(card.events_count) + '</span></div></article>';
    };

    function legacyCardHtmlBase(card) {
      const previewTags = (card.tags || []).slice(0, CARD_TAG_LIMIT);
      const extraTags = (card.tags || []).length - previewTags.length;
      const tagsHtml = previewTags.length
        ? previewTags.map((tag) => '<span class="tag">' + escapeHtml(tag) + '</span>').join('') + (extraTags > 0 ? '<span class="tag">+' + extraTags + '</span>' : '')
        : '<span class="tag tag--muted">Р‘Р•Р— РњР•РўРћРљ</span>';
      const headingHtml = buildCardHeadingHtml(card);
      return '<article class="card" draggable="true" data-card-id="' + escapeHtml(card.id) + '" data-indicator="' + escapeHtml(card.indicator) + '" data-status="' + escapeHtml(card.status) + '" data-blink="' + (card.is_blinking ? 'true' : 'false') + '">' + headingHtml + '<div class="card__desc">' + escapeHtml(card.description || 'РћРїРёСЃР°РЅРёРµ РЅРµ СѓРєР°Р·Р°РЅРѕ') + '</div><div class="card__signal"><span class="card__signal-label"><span class="lamp" data-indicator="' + escapeHtml(card.indicator) + '"></span><span>РЎРР“Рќ</span></span><span class="card__signal-value">' + durationToMarkup(card.remaining_seconds, false) + '</span></div><div class="card__tags">' + tagsHtml + '</div><div class="meta-line"><span>Р¤РђР™Р›Р« ' + escapeHtml(card.attachment_count) + '</span><span>Р–РЈР РќРђР› ' + escapeHtml(card.events_count) + '</span></div></article>';
    }

    function legacyRenderCardHtmlBase(card) {
      const normalizedTags = normalizeDraftTags(card.tag_items || card.tags || []);
      const previewTags = normalizedTags.slice(0, CARD_TAG_LIMIT);
      const extraTags = normalizedTags.length - previewTags.length;
      const tagsHtml = previewTags.length
        ? previewTags.map((tag) => '<span class="tag" data-tag-color="' + escapeHtml(tag.color) + '"><span class="tag__dot"></span>' + escapeHtml(tag.label) + '</span>').join('') + (extraTags > 0 ? '<span class="tag">+' + extraTags + '</span>' : '')
        : '<span class="tag tag--muted">Р‘Р•Р— РњР•РўРћРљ</span>';
      const headingHtml = buildCardHeadingHtml(card);
      const unreadBadgeHtml = cardUnreadBadgeHtml(card);
      const heatStyle = '--deadline-heat-border:' + escapeHtml(card.deadline_heat_border_color || 'rgba(83, 191, 122, 0.34)') + ';--deadline-heat-ring:' + escapeHtml(card.deadline_heat_ring_color || 'rgba(83, 191, 122, 0.08)') + ';--deadline-heat-glow:' + escapeHtml(card.deadline_heat_glow_color || 'rgba(83, 191, 122, 0.04)') + ';';
      return '<article class="card" style="' + heatStyle + '" draggable="true" data-card-id="' + escapeHtml(card.id) + '" data-indicator="' + escapeHtml(card.indicator) + '" data-status="' + escapeHtml(card.status) + '" data-blink="' + (card.is_blinking ? "true" : "false") + '" data-unread="' + (card.is_unread ? 'true' : 'false') + '" data-deadline-bucket="' + escapeHtml(card.deadline_progress_bucket ?? 0) + '" data-deadline-step="' + escapeHtml(card.deadline_progress_step_percent ?? 0) + '">' + unreadBadgeHtml + headingHtml + '<div class="card__desc">' + escapeHtml(card.description || 'РћРїРёСЃР°РЅРёРµ РЅРµ СѓРєР°Р·Р°РЅРѕ') + '</div><div class="card__signal"><span class="card__signal-label"><span class="lamp" data-indicator="' + escapeHtml(card.indicator) + '"></span><span>РЎРР“Рќ</span></span><span class="card__signal-value">' + durationToMarkup(card.remaining_seconds, false) + '</span></div><div class="card__tags">' + tagsHtml + '</div><div class="meta-line"><span>Р¤РђР™Р›Р« ' + escapeHtml(card.attachment_count) + '</span><span>Р–РЈР РќРђР› ' + escapeHtml(card.events_count) + '</span></div></article>';
    }

    function legacyCardHtmlShadow(card) {
      const previewTags = (card.tags || []).slice(0, CARD_TAG_LIMIT);
      const extraTags = (card.tags || []).length - previewTags.length;
      const tagsHtml = previewTags.length
        ? previewTags.map((tag) => '<span class="tag">' + escapeHtml(tag) + '</span>').join('') + (extraTags > 0 ? '<span class="tag">+' + extraTags + '</span>' : '')
        : '<span class="tag tag--muted">БЕЗ МЕТОК</span>';
      const headingHtml = buildCardHeadingHtml(card);
      return '<article class="card" draggable="true" data-card-id="' + escapeHtml(card.id) + '" data-indicator="' + escapeHtml(card.indicator) + '" data-status="' + escapeHtml(card.status) + '" data-blink="' + (card.is_blinking ? 'true' : 'false') + '">' + headingHtml + '<div class="card__desc">' + escapeHtml(card.description || 'Описание не указано') + '</div><div class="card__signal"><span class="card__signal-label"><span class="lamp" data-indicator="' + escapeHtml(card.indicator) + '"></span><span>СИГН</span></span><span class="card__signal-value">' + durationToMarkup(card.remaining_seconds, false) + '</span></div><div class="card__tags">' + tagsHtml + '</div><div class="meta-line"><span>ФАЙЛЫ ' + escapeHtml(card.attachment_count) + '</span><span>ЖУРНАЛ ' + escapeHtml(card.events_count) + '</span></div></article>';
    }

    function legacyRenderCardHtmlShadow(card) {
      const normalizedTags = normalizeDraftTags(card.tag_items || card.tags || []);
      const previewTags = normalizedTags.slice(0, CARD_TAG_LIMIT);
      const extraTags = normalizedTags.length - previewTags.length;
      const tagsHtml = previewTags.length
        ? previewTags.map((tag) => '<span class="tag" data-tag-color="' + escapeHtml(tag.color) + '"><span class="tag__dot"></span>' + escapeHtml(tag.label) + '</span>').join('') + (extraTags > 0 ? '<span class="tag">+' + extraTags + '</span>' : '')
        : '<span class="tag tag--muted">БЕЗ МЕТОК</span>';
      const headingHtml = buildCardHeadingHtml(card);
      const heatStyle = '--deadline-heat-border:' + escapeHtml(card.deadline_heat_border_color || 'rgba(83, 191, 122, 0.34)') + ';--deadline-heat-ring:' + escapeHtml(card.deadline_heat_ring_color || 'rgba(83, 191, 122, 0.08)') + ';--deadline-heat-glow:' + escapeHtml(card.deadline_heat_glow_color || 'rgba(83, 191, 122, 0.04)') + ';';
      return '<article class="card" style="' + heatStyle + '" draggable="true" data-card-id="' + escapeHtml(card.id) + '" data-indicator="' + escapeHtml(card.indicator) + '" data-status="' + escapeHtml(card.status) + '" data-blink="' + (card.is_blinking ? "true" : "false") + '" data-deadline-bucket="' + escapeHtml(card.deadline_progress_bucket ?? 0) + '" data-deadline-step="' + escapeHtml(card.deadline_progress_step_percent ?? 0) + '">' + headingHtml + '<div class="card__desc">' + escapeHtml(card.description || 'Описание не указано') + '</div><div class="card__signal"><span class="card__signal-label"><span class="lamp" data-indicator="' + escapeHtml(card.indicator) + '"></span><span>СИГН</span></span><span class="card__signal-value">' + durationToMarkup(card.remaining_seconds, false) + '</span></div><div class="card__tags">' + tagsHtml + '</div><div class="meta-line"><span>ФАЙЛЫ ' + escapeHtml(card.attachment_count) + '</span><span>ЖУРНАЛ ' + escapeHtml(card.events_count) + '</span></div></article>';
    }

    function refreshVehiclePanel() {
      const profile = cloneVehicleProfile(state.vehicleProfileDraft || emptyVehicleProfile());
      const summaryLines = [];
      if (profile.vin) summaryLines.push('VIN: ' + profile.vin);
      els.vehiclePanelSummary.textContent = summaryLines.join('\\n');
      els.vehiclePanelSummary.style.display = summaryLines.length ? '' : 'none';

      const vinInput = getVehicleFieldInput('vin');
      if (vinInput) vinInput.classList.toggle('vehicle-suspect', vinLooksSuspicious(profile.vin));

      if (!state.vehicleAutofillResult) renderVehicleAutofillStatus(defaultVehicleStatusText(profile), Boolean(profile?.warnings?.length || vinLooksSuspicious(profile.vin)));
    }

    async function saveCard() {
      const payload = currentCardPayload();
      if (!payload.title) return setStatus(CARD_TITLE_REQUIRED_MESSAGE, true);
      try {
        await persistCardPayload(payload);
        closeCardModal();
        await refreshSnapshot(true);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    configureCardFieldSemantics();
    consumeUrlAccessToken();
    configureOperatorIdentityUi();
    renderVehicleProfileFields();
    applyVehicleProfileToForm(emptyVehicleProfile());
    refreshRepairOrderEntry(null);
    mountStatusLine();
    bootstrapOperatorSession();
    refreshSnapshot(true);
    document.addEventListener('visibilitychange', startSnapshotPolling);
    startSnapshotPolling();
  </script>
</body>
</html>
""",
    ]
)
