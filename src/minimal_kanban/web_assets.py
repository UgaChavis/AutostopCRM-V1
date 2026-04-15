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
    .shell { display: grid; grid-template-rows: auto minmax(0, 1fr); height: 100%; min-height: 0; overflow: hidden; }
    .status-shell {
      min-height: 0;
      padding: 0;
      position: relative;
      z-index: 2;
    }
    .status-shell .message {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      width: max-content;
      max-width: 100%;
      padding: 5px 9px;
      font-size: 11px;
      line-height: 1.2;
      letter-spacing: 0.04em;
      color: var(--text-soft);
      border-color: rgba(167, 178, 132, 0.24);
      background: rgba(13, 17, 14, 0.32);
    }
    .status-shell .message::before {
      content: "";
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: rgba(167, 178, 132, 0.92);
      box-shadow: 0 0 0 1px rgba(167, 178, 132, 0.22);
      flex: 0 0 auto;
    }
    .topbar {
      border-bottom: 1px solid var(--line);
      background: rgba(0, 0, 0, 0.16);
      padding: 10px 14px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      position: relative;
      z-index: 3;
    }
    .topbar__left { display: flex; align-items: center; gap: 8px; min-width: 0; }
    .brand { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
    .brand__title {
      font-family: var(--mono);
      font-size: 17px;
      letter-spacing: 0.11em;
      font-weight: 700;
      line-height: 1.05;
    }
    .topbar__meta {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
      min-width: 0;
    }
    .brand__sub {
      color: var(--text-soft);
      font-size: 9.5px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-family: var(--mono);
      opacity: 0.68;
    }
    .topbar__actions {
      display: flex;
      gap: 5px;
      row-gap: 5px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .topbar__actions .btn {
      min-height: 32px;
      padding: 7px 10px;
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
    #repairOrderCloseButton[data-close-available="false"],
    #repairOrderCloseButton[data-close-available="false"]:hover {
      color: rgba(200, 198, 187, 0.46);
      border-color: rgba(255,255,255,0.08);
      background: rgba(0,0,0,0.08);
      box-shadow: none;
      cursor: default;
    }
    .gear-button {
      width: 40px;
      height: 40px;
      padding: 0;
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
      width: 18px;
      height: 18px;
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
      .agent-dock {
        position: fixed;
        left: 28px;
        bottom: 114px;
        z-index: 12;
      }
      .agent-dock__button {
        position: relative;
        width: 56px;
        height: 56px;
        padding: 0;
        border: 1px solid rgba(165, 176, 122, 0.66);
        background:
          linear-gradient(180deg, rgba(255,255,255,0.06), transparent 24%),
          rgba(18, 24, 19, 0.94);
        color: var(--text);
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-family: var(--mono);
        font-size: 14px;
        letter-spacing: 0.12em;
        cursor: pointer;
        box-shadow:
          inset 0 1px 0 rgba(255,255,255,0.08),
          0 0 0 1px rgba(0,0,0,0.18),
          0 8px 20px rgba(0,0,0,0.22);
        transition: border-color 120ms ease, transform 120ms ease, background 120ms ease, box-shadow 120ms ease;
      }
      .agent-dock__button:hover {
        border-color: var(--accent);
        transform: translateY(-1px);
      }
      .agent-dock__button::after {
        content: "";
        position: absolute;
        right: 8px;
        bottom: 8px;
        width: 7px;
        height: 7px;
        border-radius: 999px;
        background: rgba(144, 155, 121, 0.56);
        box-shadow: 0 0 0 1px rgba(0,0,0,0.18);
      }
      .agent-dock__button[data-state="busy"] {
        border-color: var(--accent);
        box-shadow:
          inset 0 1px 0 rgba(255,255,255,0.08),
          0 0 0 1px rgba(200, 210, 166, 0.22),
          0 10px 24px rgba(0,0,0,0.24);
      }
      .agent-dock__button[data-state="online"]::after { background: rgba(115, 182, 107, 0.92); }
      .agent-dock__button[data-state="busy"]::after { background: rgba(214, 175, 55, 0.94); }
      .agent-dock__button[data-state="error"]::after { background: rgba(207, 91, 75, 0.94); }
      .card-agent-button {
        width: 42px;
        min-width: 42px;
        height: 34px;
        padding: 0;
        position: relative;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-family: var(--mono);
        font-size: 0;
        line-height: 0;
        letter-spacing: 0;
        color: var(--text);
        border-color: rgba(167, 178, 132, 0.42);
        background:
          linear-gradient(180deg, rgba(255,255,255,0.05), transparent 24%),
          rgba(0,0,0,0.12);
      }
      .card-agent-button::before {
        content: "";
        width: 8px;
        height: 8px;
        border-radius: 999px;
        background: rgba(144, 155, 121, 0.56);
        box-shadow: 0 0 0 1px rgba(0,0,0,0.18);
        flex: 0 0 auto;
      }
      .card-agent-button[data-state="online"]::before { background: rgba(115, 182, 107, 0.92); }
      .card-agent-button[data-state="busy"]::before { background: rgba(214, 175, 55, 0.94); }
      .card-agent-button[data-state="error"]::before { background: rgba(207, 91, 75, 0.94); }
      .card-agent-button[data-state="idle"]::before { background: rgba(144, 155, 121, 0.56); }
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
    .column[draggable="true"] { cursor: grab; }
    .column[draggable="true"]:active { cursor: grabbing; }
    .column.is-drop-target { outline: 1px solid var(--accent); }
    .column.is-column-drop-target { outline: 1px dashed rgba(167, 178, 132, 0.92); outline-offset: 2px; }
    .column.is-column-dragging { opacity: 0.72; }
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
      font-size: calc(13px * var(--board-scale));
      line-height: 1.28;
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
    .lamp {
      width: calc(13px * var(--board-scale));
      height: calc(13px * var(--board-scale));
      border: 1px solid #283126;
      background: #6f786e;
      flex: 0 0 auto;
    }
    .lamp[data-indicator="green"] { background: var(--ok); }
    .lamp[data-indicator="yellow"] { background: var(--warn); }
    .lamp[data-indicator="red"] {
      background: var(--danger);
      box-shadow: 0 0 0 1px rgba(207, 91, 75, 0.2);
      animation: lamp-red-pulse 1.8s ease-in-out infinite;
      transform-origin: center;
      will-change: transform, box-shadow;
    }
    @keyframes lamp-red-pulse {
      0%, 100% {
        transform: scale(1);
        box-shadow: 0 0 0 1px rgba(207, 91, 75, 0.2), 0 0 0 rgba(207, 91, 75, 0);
      }
      50% {
        transform: scale(1.18);
        box-shadow: 0 0 0 1px rgba(207, 91, 75, 0.36), 0 0 12px rgba(207, 91, 75, 0.42);
      }
    }
    @media (prefers-reduced-motion: reduce) {
      .lamp[data-indicator="red"] {
        animation: none;
      }
    }
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
      width: min(1200px, calc(100% - 28px));
      padding: 14px;
      transform: none;
      gap: 10px;
    }
    .dialog__head, .dialog__foot, .dialog__tabs {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }
    .dialog__foot--card {
      padding-top: 4px;
      border-top: 1px solid rgba(115, 126, 105, 0.16);
    }
    .dialog__foot-group {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      align-items: center;
    }
    .dialog__foot-group--danger {
      margin-right: auto;
    }
    .dialog__foot-group--main {
      justify-content: flex-end;
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
      gap: 8px;
      align-items: center;
    }
    .dialog__head--card > .btn {
      position: relative;
      z-index: 2;
      pointer-events: auto;
    }
    .dialog__title-wrap {
      min-width: 0;
      display: grid;
      gap: 2px;
    }
    .dialog__title-prefix {
      font-family: var(--mono);
      font-size: 9px;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: rgba(200, 198, 187, 0.62);
    }
    .dialog__title--card {
      min-width: 0;
      max-width: 100%;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      line-height: 1.2;
      font-size: 15px;
      letter-spacing: 0.07em;
    }
    .dialog__head--repair-order .dialog__title-wrap {
      gap: 6px;
    }
    .repair-order-headline {
      display: flex;
      align-items: center;
      gap: 9px;
      min-width: 0;
      flex-wrap: wrap;
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
      grid-template-columns: minmax(648px, 756px) minmax(264px, 308px);
      gap: 14px;
      align-items: start;
      justify-content: center;
    }
    .overview-main {
      display: grid;
      gap: 9px;
      min-width: 0;
      max-width: 756px;
      position: relative;
      z-index: 1;
    }
    .overview-main__meta {
      display: grid;
      grid-template-columns: minmax(168px, 184px) minmax(0, 1fr);
      gap: 8px;
      align-items: start;
    }
    .dialog__tabs--card {
      align-items: flex-end;
      padding-bottom: 6px;
      border-bottom: 1px solid rgba(115, 126, 105, 0.16);
    }
    #cardMetaLine {
      color: rgba(200, 198, 187, 0.76);
      font-size: 10px;
      line-height: 1.35;
      letter-spacing: 0.03em;
      text-align: right;
      opacity: 0.82;
    }
    .stack { display: grid; gap: 12px; }
    .field { display: grid; gap: 5px; }
    .grid--overview { grid-template-columns: minmax(176px, 0.62fr) minmax(0, 1fr); gap: 7px; }
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
    input[type="text"], input[type="password"], input[type="search"], input[type="month"], textarea, select, input[type="number"] {
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
      min-height: 136px;
      height: 136px;
      max-height: clamp(440px, 56vh, 720px);
      padding: 10px 12px;
      line-height: 1.54;
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
      gap: 6px;
      padding: 8px;
      align-content: start;
      min-width: 0;
    }
    .signal-panel .panel-title,
    .tags-panel .panel-title {
      font-size: 11px;
      letter-spacing: 0.1em;
    }
    .signal-preview {
      border: 1px solid var(--line-soft);
      background:
        linear-gradient(180deg, rgba(255,255,255,0.03), transparent 38%),
        rgba(0,0,0,0.18);
      min-height: 28px;
      padding: 3px 7px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-family: var(--mono);
      font-size: 11px;
      line-height: 1.15;
      letter-spacing: 0.06em;
      color: var(--text);
    }
    .signal-preview .time-readout { gap: 6px; }
    .signal-preview .time-readout__unit { font-size: 0.58em; }
    .signal-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 3px;
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
      gap: 5px;
    }
    .signal-cell__label {
      font-family: var(--mono);
      font-size: 9px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--text-soft);
    }
    .signal-stepper {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto auto;
      align-items: center;
      min-height: 38px;
      border: 1px solid var(--line-soft);
      background:
        linear-gradient(180deg, rgba(255,255,255,0.03), transparent 40%),
        rgba(0,0,0,0.16);
    }
    .signal-stepper__button {
      min-width: 32px;
      min-height: 32px;
      border: 0;
      background: rgba(255,255,255,0.02);
      color: var(--text);
      font-family: var(--mono);
      font-size: 14px;
      font-weight: 800;
      line-height: 1;
      cursor: pointer;
    }
    .signal-stepper__button:disabled {
      opacity: 0.28;
      cursor: not-allowed;
    }
    .signal-stepper__button:hover:not(:disabled) {
      background: rgba(255,255,255,0.05);
    }
    .signal-stepper__value {
      min-width: 0;
      min-height: 32px;
      display: grid;
      place-items: center;
      padding: 0 10px;
      font-family: var(--mono);
      font-size: 21px;
      font-weight: 700;
      color: var(--text);
      letter-spacing: 0.02em;
    }
    .signal-stepper__unit {
      min-width: 22px;
      padding: 0 8px 0 0;
      font-family: var(--mono);
      font-size: 10px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--text-soft);
      text-align: center;
      pointer-events: none;
    }
    .signal-input--hidden {
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
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
      gap: 4px;
      padding: 6px;
      align-content: start;
      min-width: 0;
    }
    .tags-panel__head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 4px;
    }
    .tag-limit {
      min-width: 34px;
      padding: 2px 4px;
      border: 1px solid var(--line-soft);
      background: rgba(0,0,0,0.16);
      font-family: var(--mono);
      font-size: 8.5px;
      letter-spacing: 0.06em;
      text-align: center;
      color: var(--text-soft);
    }
    .tag-limit[data-limit-state="full"] {
      border-color: rgba(193, 162, 84, 0.48);
      color: #d7c58a;
      background: rgba(193, 162, 84, 0.12);
    }
    .tag-controls {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 5px;
      align-items: center;
    }
    .tag-entry {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 28px;
      gap: 3px;
      align-items: center;
    }
    .tag-list {
      display: flex;
      flex-wrap: wrap;
      gap: 3px;
      align-content: flex-start;
      min-height: 20px;
      padding: 0;
    }
    .tag-entry input[type="text"] {
      min-height: 28px;
      padding: 4px 8px;
      font-family: var(--mono);
      font-size: 10.5px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    .tag-entry input[type="text"][disabled] {
      opacity: 0.62;
      cursor: not-allowed;
    }
    .tag-entry .btn {
      min-width: 28px;
      min-height: 28px;
      padding: 0;
      display: grid;
      place-items: center;
      font-size: 11px;
    }
    .tag-entry .btn[disabled] {
      opacity: 0.44;
      cursor: not-allowed;
    }
    .tag-suggestions {
      display: flex;
      flex-wrap: wrap;
      gap: 3px;
      align-content: flex-start;
      min-height: 20px;
    }
    .tag-color-picker {
      display: inline-flex;
      align-items: center;
      gap: 3px;
      min-height: 28px;
      padding: 0 1px 0 0;
    }
    .tag-color-option {
      width: 14px;
      height: 14px;
      border: 1px solid rgba(255,255,255,0.14);
      background: rgba(0, 0, 0, 0.16);
      padding: 0;
      cursor: pointer;
      position: relative;
    }
    .tag-color-option::after {
      content: "";
      position: absolute;
      inset: 2.5px;
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
      min-height: 20px;
      padding: 2px 6px;
      font-family: var(--mono);
      font-size: 8.5px;
      font-weight: 700;
      letter-spacing: 0.05em;
      line-height: 1;
    }
    .tag-list .tag--muted {
      justify-content: center;
      min-width: 84px;
      color: var(--text-soft);
      background: rgba(0,0,0,0.14);
      border-color: var(--line-soft);
    }
    .tag-suggestion {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 20px;
      border: 1px solid var(--line-soft);
      background: rgba(0, 0, 0, 0.16);
      color: var(--text-soft);
      padding: 2px 6px;
      font-family: var(--mono);
      font-size: 8.5px;
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
      gap: 6px;
      width: min(100%, 308px);
      max-width: 308px;
      justify-self: end;
      margin-left: 2px;
      padding: 8px;
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
      left: -10px;
      width: 1px;
      background: rgba(115, 126, 105, 0.16);
    }
    .vehicle-panel__head {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: start;
      gap: 8px;
    }
    .vehicle-panel__summary {
      color: var(--text-soft);
      font-family: var(--mono);
      font-size: 9px;
      line-height: 1.32;
      letter-spacing: 0.03em;
      white-space: pre-wrap;
      opacity: 0.84;
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
      gap: 5px;
      max-height: none;
      overflow: visible;
      padding-right: 0;
    }
    .vehicle-panel__repair {
      display: grid;
      gap: 5px;
      padding-top: 7px;
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
    #repairOrderPaymentsModal {
      z-index: 15;
    }
    .vehicle-panel__repair-note {
      color: var(--text-soft);
      font-family: var(--mono);
      font-size: 11px;
      line-height: 1.45;
      letter-spacing: 0.03em;
    }
    .dialog--repair-order {
      width: min(1320px, calc(100% - 16px));
      max-height: min(93vh, 940px);
      padding: 0;
      gap: 0;
      overflow: hidden;
      grid-template-rows: auto minmax(0, 1fr) auto;
      background:
        linear-gradient(180deg, rgba(255,255,255,0.02), transparent 18%),
        var(--bg-panel);
    }
    .dialog--repair-order .dialog__head {
      padding: 13px 15px 10px;
      margin: 0;
      border-bottom: 1px solid rgba(115, 126, 105, 0.2);
      background: rgba(0, 0, 0, 0.08);
    }
    .repair-order-shell {
      display: grid;
      gap: 7px;
      padding: 9px 11px 11px;
      overflow: auto;
      min-height: 0;
      align-content: start;
    }
    .repair-order-toolbar {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 8px;
      flex-wrap: wrap;
    }
    .repair-order-toolbar .btn {
      min-height: 34px;
      padding: 7px 11px;
    }
    .repair-order-groups {
      display: grid;
      grid-template-columns: minmax(168px, 0.56fr) minmax(324px, 1.08fr) minmax(520px, 1.78fr);
      gap: 8px;
      align-items: stretch;
    }
    .repair-order-card,
    .repair-order-table-card {
      display: grid;
      gap: 6px;
      padding: 8px;
      border: 1px solid rgba(116, 126, 106, 0.15);
      background:
        linear-gradient(180deg, rgba(255,255,255,0.02), transparent 24%),
        rgba(0, 0, 0, 0.08);
    }
    .repair-order-card__grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 6px;
    }
    .repair-order-card__grid--document {
      grid-template-columns: 46px repeat(3, minmax(0, 1fr));
      align-items: end;
    }
    .repair-order-card__grid--payment {
      grid-template-columns: minmax(0, 1fr) minmax(164px, 0.72fr);
      align-items: end;
    }
    .repair-order-card__grid--client {
      grid-template-columns: minmax(0, 1.92fr) minmax(152px, 0.58fr);
      align-items: end;
    }
    .repair-order-card__grid--vehicle {
      grid-template-columns: minmax(0, 1.56fr) minmax(112px, 0.48fr) minmax(0, 1.42fr) minmax(94px, 0.36fr);
      align-items: end;
    }
    .repair-order-card__grid--document .field--compact input[type="text"],
    .repair-order-card__grid--payment .field--compact input[type="text"],
    .repair-order-card__grid--payment .field--compact select,
    .repair-order-card__grid--client .field--compact input[type="text"],
    .repair-order-card__grid--vehicle .field--compact input[type="text"] {
      min-height: 29px;
      padding: 4px 7px;
      font-size: 12px;
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
      font-size: 13px;
    }
    .repair-order-card__grid--document .field--compact label,
    .repair-order-card__grid--payment .field--compact label,
    .repair-order-card__grid--vehicle .field--compact label,
    .repair-order-card__grid--client .field--compact label {
      font-size: 10px;
      letter-spacing: 0.06em;
      white-space: nowrap;
    }
    .repair-order-client-info textarea {
      min-height: 112px;
      height: 112px;
      max-height: 168px;
      line-height: 1.4;
      padding: 7px 9px;
      font-size: 12.5px;
    }
    .repair-order-status {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 24px;
      padding: 4px 10px;
      border: 1px solid rgba(123, 166, 113, 0.36);
      background: rgba(81, 122, 72, 0.18);
      color: #e6f1db;
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.04);
      font-family: var(--mono);
      font-size: 10px;
      letter-spacing: 0.08em;
      font-weight: 700;
      text-transform: uppercase;
      white-space: nowrap;
    }
    .repair-order-status[data-status="closed"] {
      border-color: rgba(181, 109, 97, 0.4);
      background: rgba(119, 50, 43, 0.18);
      color: #f4dcd7;
    }
    .repair-order-section-bar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      flex-wrap: wrap;
    }
    .repair-order-section-bar .btn {
      min-height: 30px;
      padding: 5px 9px;
    }
    .repair-order-table-wrap {
      border: 1px solid rgba(116, 126, 106, 0.14);
      background: rgba(0, 0, 0, 0.07);
      overflow: hidden;
    }
    .repair-order-table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    .repair-order-table th {
      padding: 6px 8px;
      color: var(--text-soft);
      font-family: var(--mono);
      font-size: 9.25px;
      font-weight: 600;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      text-align: left;
      border-bottom: 1px solid rgba(116, 126, 106, 0.18);
      background: rgba(255, 255, 255, 0.02);
    }
    .repair-order-table td {
      padding: 2px 4px;
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
      width: 44px;
      text-align: center;
    }
    .repair-order-table__input {
      width: 100%;
      min-width: 0;
      border: 1px solid transparent;
      border-bottom-color: rgba(116, 126, 106, 0.16);
      background: transparent;
      color: var(--text);
      padding: 5px 7px;
      min-height: 30px;
      outline: none;
      font-size: 12.25px;
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
      min-height: 30px;
      display: flex;
      align-items: center;
      justify-content: flex-end;
      padding: 0 7px;
      color: var(--text);
      font-family: var(--mono);
      font-size: 11.5px;
      font-variant-numeric: tabular-nums;
    }
    .repair-order-cell-total[data-empty="true"] {
      color: rgba(200, 198, 187, 0.56);
    }
    .repair-order-row-remove {
      width: 24px;
      min-width: 24px;
      height: 24px;
      padding: 0;
      font-size: 13px;
      line-height: 1;
    }
    .repair-order-subtotal {
      display: flex;
      justify-content: flex-end;
      align-items: center;
      gap: 8px;
      padding: 6px 2px 0;
      border-top: 1px solid rgba(116, 126, 106, 0.12);
      color: var(--text-soft);
      font-family: var(--mono);
      font-size: 10px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }
    .repair-order-subtotal strong {
      min-width: 124px;
      text-align: right;
      color: var(--text);
      font-size: 14px;
      letter-spacing: 0.02em;
      font-variant-numeric: tabular-nums;
    }
    .repair-order-footer {
      padding: 8px 11px 9px;
      margin: 0;
      border-top: 1px solid rgba(115, 126, 105, 0.18);
      background:
        linear-gradient(180deg, rgba(255,255,255,0.02), transparent 55%),
        rgba(17, 23, 19, 0.94);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 9px;
    }
    .repair-order-footer__totals {
      display: grid;
      grid-auto-flow: column;
      grid-auto-columns: minmax(96px, max-content);
      align-items: end;
      gap: 8px;
      flex: 1 1 auto;
      min-width: 0;
    }
    .repair-order-total {
      display: grid;
      gap: 2px;
      min-width: 0;
    }
    .repair-order-total.is-hidden {
      display: none;
    }
    .repair-order-total span {
      color: var(--text-soft);
      font-family: var(--mono);
      font-size: 9.25px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .repair-order-total strong {
      color: var(--text);
      font-size: 15px;
      font-variant-numeric: tabular-nums;
      line-height: 1.1;
    }
    .repair-order-total--cashless {
      padding: 5px 8px;
      border: 1px solid rgba(116, 126, 106, 0.16);
      background: rgba(255, 255, 255, 0.02);
    }
    .repair-order-total--cashless strong {
      font-size: 14px;
    }
    .repair-order-total--cash {
      padding: 6px 9px;
      border: 1px solid rgba(116, 126, 106, 0.2);
      background: rgba(255, 255, 255, 0.03);
    }
    .repair-order-total--cash strong {
      font-size: 18px;
    }
    .repair-order-total--summary {
      padding: 6px 9px;
      border: 1px solid rgba(116, 126, 106, 0.2);
      background: rgba(255, 255, 255, 0.03);
    }
    .repair-order-total--summary strong {
      font-size: 17px;
    }
    .repair-order-total--grand {
      padding: 8px 11px;
      border: 1px solid rgba(140, 151, 109, 0.34);
      background: rgba(140, 151, 109, 0.12);
    }
    .repair-order-total--grand strong {
      font-size: 24px;
      color: #f7f4e6;
    }
    .repair-order-footer__actions {
      display: flex;
      gap: 5px;
      flex-wrap: wrap;
      justify-content: flex-end;
      margin-left: auto;
      flex: 0 0 auto;
      align-items: stretch;
    }
    .repair-order-footer__actions .btn {
      min-width: 100px;
      min-height: 36px;
      padding: 7px 10px;
    }
    .repair-order-hidden-fields {
      display: none !important;
    }
    .repair-order-field--prepayment {
      display: none;
    }
    .repair-order-money-button {
      min-width: 38px !important;
      width: 38px;
      padding: 0;
      border-color: rgba(140, 151, 109, 0.3);
      background: rgba(140, 151, 109, 0.08);
      color: #f0eddf;
      font-size: 16px;
      font-weight: 700;
      line-height: 1;
    }
    .repair-order-money-button:hover {
      border-color: rgba(167, 178, 132, 0.42);
      background: rgba(140, 151, 109, 0.16);
    }
    .dialog--repair-order-payments {
        width: min(748px, calc(100% - 20px));
        max-height: min(82vh, 760px);
        padding: 0;
        gap: 0;
        overflow: hidden;
      }
      .dialog--agent {
        width: min(720px, calc(100% - 24px));
        max-height: min(84vh, 760px);
        padding: 0;
        gap: 0;
        overflow: hidden;
        display: flex;
        flex-direction: column;
      }
      .dialog--ai-entry {
        width: min(860px, calc(100% - 24px));
      }
      .dialog--ai-chat {
        width: min(1360px, calc(100% - 24px));
        height: min(94vh, 960px);
        max-height: min(94vh, 960px);
        padding: 0;
        gap: 0;
        overflow: hidden;
        display: flex;
        flex-direction: column;
      }
      .dialog--agent .dialog__head {
        padding: 11px 12px 9px;
        margin: 0;
        border-bottom: 1px solid rgba(115, 126, 105, 0.18);
        background: rgba(0, 0, 0, 0.08);
      }
      .agent-shell {
        display: flex;
        flex-direction: column;
        gap: 8px;
        min-height: 0;
        padding: 11px;
        overflow: hidden;
        flex: 1 1 auto;
      }
      .agent-headline {
        display: flex;
        justify-content: space-between;
        gap: 8px;
        align-items: center;
        flex-wrap: wrap;
        font-family: var(--mono);
        font-size: 10px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
      }
      .agent-context {
        color: var(--text-soft);
        max-width: calc(100% - 110px);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .agent-status {
        color: var(--muted);
        display: inline-flex;
        align-items: center;
        gap: 6px;
        white-space: nowrap;
      }
      .agent-status::before {
        content: "";
        width: 7px;
        height: 7px;
        border-radius: 999px;
        background: rgba(129, 138, 123, 0.76);
        box-shadow: 0 0 0 1px rgba(0,0,0,0.18);
      }
      .agent-status[data-state="online"] { color: var(--accent); }
      .agent-status[data-state="busy"] { color: var(--text); }
      .agent-status[data-state="error"] { color: #e0a19c; }
      .agent-status[data-state="online"]::before { background: rgba(115, 182, 107, 0.92); }
      .agent-status[data-state="busy"]::before { background: rgba(214, 175, 55, 0.94); }
      .agent-status[data-state="error"]::before { background: rgba(207, 91, 75, 0.94); }
      .agent-shortcuts {
        display: flex;
        flex-wrap: wrap;
        gap: 4px;
      }
      .agent-shortcut {
        min-height: 24px;
        padding: 4px 8px;
        border: 1px solid rgba(116, 126, 106, 0.22);
        background: rgba(0, 0, 0, 0.08);
        color: var(--text-soft);
        cursor: pointer;
        font-family: var(--mono);
        font-size: 9.5px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
      }
      .agent-shortcut:hover {
        color: var(--text);
        border-color: rgba(167, 178, 132, 0.42);
        background: rgba(167, 178, 132, 0.08);
      }
      .ai-entry-shell {
        display: flex;
        flex-direction: column;
        gap: 10px;
        min-height: 0;
        padding: 12px;
        overflow: hidden;
        flex: 1 1 auto;
      }
      .ai-entry-summary {
        font-size: 12px;
        line-height: 1.5;
        color: var(--text-soft);
        border: 1px solid rgba(116, 126, 106, 0.18);
        background: rgba(0, 0, 0, 0.08);
        padding: 9px 10px;
      }
      .ai-entry-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 8px;
      }
      .ai-entry-tile {
        min-height: 118px;
        padding: 10px 11px;
        border: 1px solid rgba(116, 126, 106, 0.22);
        background:
          linear-gradient(180deg, rgba(255,255,255,0.04), transparent 30%),
          rgba(0, 0, 0, 0.1);
        color: var(--text);
        display: flex;
        flex-direction: column;
        gap: 6px;
        text-align: left;
        cursor: pointer;
        font-family: var(--mono);
        transition: transform 120ms ease, border-color 120ms ease, background 120ms ease;
      }
      .ai-entry-tile:hover {
        transform: translateY(-1px);
        border-color: rgba(167, 178, 132, 0.52);
      }
      .ai-entry-tile.is-selected {
        border-color: rgba(167, 178, 132, 0.7);
        background:
          linear-gradient(180deg, rgba(167, 178, 132, 0.12), transparent 30%),
          rgba(0, 0, 0, 0.14);
      }
      .ai-entry-tile[data-state="online"] { border-color: rgba(115, 182, 107, 0.42); }
      .ai-entry-tile[data-state="busy"] { border-color: rgba(214, 175, 55, 0.44); }
      .ai-entry-tile[data-state="error"] { border-color: rgba(207, 91, 75, 0.48); }
      .ai-entry-tile[data-state="idle"] { opacity: 0.7; }
      .ai-entry-tile__title {
        font-size: 11px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--text);
      }
      .ai-entry-tile__description {
        font-size: 12px;
        line-height: 1.45;
        color: rgba(229, 228, 214, 0.92);
        flex: 1 1 auto;
      }
      .ai-entry-tile__meta {
        font-size: 10px;
        line-height: 1.45;
        color: var(--text-soft);
        flex: 0 0 auto;
      }
      .ai-entry-tile__state {
        font-size: 9px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--muted);
      }
      .ai-entry-details {
        display: flex;
        flex-direction: column;
        gap: 6px;
        min-height: 0;
      }
      .ai-entry-detail-title {
        font-family: var(--mono);
        font-size: 10px;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: var(--text-soft);
      }
      .ai-entry-foot {
        justify-content: space-between;
        align-items: center;
      }
      .ai-entry-legacy {
        margin-left: auto;
      }
      .ai-chat-window {
        display: flex;
        flex-direction: column;
        min-height: 0;
        flex: 1 1 auto;
        overflow: hidden;
        height: 100%;
      }
      .ai-chat-window__layout {
        display: grid;
        grid-template-rows: auto auto minmax(0, 1fr) auto;
        min-height: 0;
        flex: 1 1 auto;
        height: 100%;
      }
      .ai-chat-window__head {
        padding: 12px 14px 10px;
        margin: 0;
        border-bottom: 1px solid rgba(115, 126, 105, 0.18);
        background: rgba(0, 0, 0, 0.08);
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 12px;
        flex-wrap: wrap;
      }
      .ai-chat-window__title-block {
        display: grid;
        gap: 4px;
        min-width: 0;
        flex: 1 1 260px;
      }
      .ai-chat-window__title {
        font-family: var(--mono);
        font-size: 12px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--text);
      }
      .ai-chat-window__context {
        font-family: var(--mono);
        font-size: 10px;
        line-height: 1.35;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--accent);
      }
      .ai-chat-window__subtitle {
        font-size: 11px;
        line-height: 1.45;
        color: var(--text-soft);
      }
      .ai-chat-window__controls {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
        justify-content: flex-end;
      }
      .ai-chat-window__settings {
        min-height: 0;
        border-bottom: 1px solid rgba(115, 126, 105, 0.16);
        background: rgba(0, 0, 0, 0.05);
        padding: 12px 14px 14px;
        display: none;
        gap: 10px;
      }
      .ai-chat-window__settings.is-open {
        display: grid;
      }
      .ai-chat-window__settings-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        flex-wrap: wrap;
      }
      .ai-chat-window__settings-title {
        font-family: var(--mono);
        font-size: 10px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--text-soft);
      }
      .ai-chat-window__settings-note {
        font-size: 11px;
        line-height: 1.45;
        color: var(--text-soft);
      }
      .ai-chat-window__settings-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 10px;
      }
      .ai-chat-window__profile-card {
        border: 1px solid rgba(116, 126, 106, 0.18);
        background: rgba(0, 0, 0, 0.08);
        border-radius: 10px;
        padding: 10px 12px;
        display: grid;
        gap: 6px;
        min-width: 0;
      }
      .ai-chat-window__profile-label {
        font-family: var(--mono);
        font-size: 10px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--text-soft);
      }
      .ai-chat-window__profile-value {
        font-size: 11px;
        line-height: 1.45;
        color: var(--text);
        white-space: pre-wrap;
        word-break: break-word;
      }
      .ai-chat-window__profile-card--editable {
        grid-column: span 1;
      }
      .ai-chat-window__profile-card--wide {
        grid-column: span 2;
      }
      .ai-chat-window__profile-input {
        min-height: 110px;
        resize: vertical;
        width: 100%;
      }
      .ai-chat-window__body {
        min-height: 0;
        flex: 1 1 auto;
        display: flex;
        flex-direction: column;
        overflow: hidden;
      }
      .ai-chat-window__messages-pane {
        min-height: 0;
        flex: 1 1 auto;
        display: flex;
        overflow: hidden;
      }
      .ai-chat-window__messages {
        min-height: 0;
        flex: 1 1 auto;
        overflow-y: auto;
        padding: 14px;
        display: grid;
        gap: 10px;
        align-content: start;
        background:
          linear-gradient(180deg, rgba(255,255,255,0.02), transparent 20%),
          rgba(0, 0, 0, 0.04);
        scrollbar-gutter: stable;
        overscroll-behavior: contain;
      }
      .ai-chat-window__message {
        border: 1px solid rgba(116, 126, 106, 0.18);
        background: rgba(0, 0, 0, 0.08);
        padding: 10px 12px;
        border-radius: 10px;
        display: grid;
        gap: 6px;
        max-width: min(100%, 900px);
      }
      .ai-chat-window__message[data-role="assistant"] {
        margin-right: auto;
        border-color: rgba(167, 178, 132, 0.26);
      }
      .ai-chat-window__message[data-role="user"] {
        margin-left: auto;
        border-color: rgba(108, 128, 196, 0.3);
      }
      .ai-chat-window__message-title {
        font-family: var(--mono);
        font-size: 10px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--text-soft);
      }
      .ai-chat-window__message-text {
        font-size: 12px;
        line-height: 1.55;
        color: var(--text);
        white-space: normal;
        word-break: break-word;
        overflow-wrap: anywhere;
        user-select: text;
        -webkit-user-select: text;
      }
      .ai-chat-window__message-text p {
        margin: 0 0 8px;
      }
      .ai-chat-window__message-text p:last-child {
        margin-bottom: 0;
      }
      .ai-chat-window__message-text ul,
      .ai-chat-window__message-text ol {
        margin: 0 0 8px 18px;
        padding: 0;
      }
      .ai-chat-window__message-text li {
        margin: 0 0 4px;
      }
      .ai-chat-window__message-text code {
        padding: 1px 4px;
        border: 1px solid rgba(116, 126, 106, 0.22);
        border-radius: 5px;
        background: rgba(0, 0, 0, 0.14);
        font-family: var(--mono);
        font-size: 11px;
      }
      .ai-chat-window__message-text pre {
        margin: 0 0 8px;
        padding: 10px 12px;
        border: 1px solid rgba(116, 126, 106, 0.22);
        border-radius: 10px;
        background: rgba(0, 0, 0, 0.16);
        overflow: auto;
        white-space: pre;
        user-select: text;
        -webkit-user-select: text;
      }
      .ai-chat-window__message-text pre code {
        padding: 0;
        border: 0;
        background: transparent;
        font-size: 11px;
        line-height: 1.5;
        white-space: pre;
      }
      .ai-chat-window__message-text a {
        color: var(--accent);
        text-decoration: underline;
        text-underline-offset: 2px;
      }
      .ai-chat-window__message-text strong {
        font-weight: 700;
        color: var(--text);
      }
      .ai-chat-window__message-text em {
        font-style: italic;
      }
      .ai-chat-window__message-meta {
        font-family: var(--mono);
        font-size: 10px;
        line-height: 1.35;
        letter-spacing: 0.04em;
        color: var(--text-soft);
        opacity: 0.8;
      }
      .ai-chat-window__composer-pane {
        min-height: 0;
        flex: 0 0 auto;
      }
      .ai-chat-window__composer {
        border-top: 1px solid rgba(115, 126, 105, 0.18);
        background: rgba(0, 0, 0, 0.08);
        padding: 12px 14px 14px;
        display: grid;
        gap: 8px;
        min-height: 0;
      }
      .ai-chat-window__composer-label {
        font-family: var(--mono);
        font-size: 10px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--text-soft);
      }
      .ai-chat-window__input {
        min-height: 96px;
        resize: vertical;
        width: 100%;
        max-height: 30vh;
      }
      .ai-chat-window__composer-foot {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        flex-wrap: wrap;
      }
      .agent-field textarea {
        min-height: 80px;
        height: 80px;
        max-height: 180px;
        resize: none;
        overflow-y: auto;
      }
      .agent-actions-row {
        display: grid;
        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
        gap: 10px;
        align-items: stretch;
      }
      .agent-tasks-launch {
        min-height: 54px;
        padding: 10px 12px;
        border: 1px solid rgba(116, 126, 106, 0.24);
        background: rgba(12, 16, 13, 0.5);
        display: grid;
        justify-items: start;
        align-content: center;
        gap: 4px;
        text-align: left;
      }
      .agent-tasks-launch:hover {
        border-color: rgba(182, 177, 116, 0.44);
        background: rgba(43, 49, 33, 0.2);
      }
      .agent-tasks-launch__title {
        font-family: var(--mono);
        font-size: 13px;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: var(--text-strong);
      }
      .agent-tasks-launch__meta {
        font-size: 10px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--muted);
      }
      .agent-autofill-panel {
        min-height: 54px;
        padding: 10px 12px;
        border: 1px solid rgba(116, 126, 106, 0.24);
        background: rgba(12, 16, 13, 0.5);
        display: grid;
        gap: 6px;
      }
      .agent-autofill-panel__top {
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        gap: 8px;
        align-items: center;
      }
      .agent-autofill-status {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        font-family: var(--mono);
        font-size: 10px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--muted);
      }
      .agent-autofill-status::before {
        content: "";
        width: 7px;
        height: 7px;
        border-radius: 999px;
        background: rgba(129, 138, 123, 0.76);
      }
      .agent-autofill-status[data-state="online"]::before,
      .agent-autofill-status[data-state="active"]::before { background: rgba(115, 182, 107, 0.92); }
      .agent-autofill-status[data-state="waiting"]::before { background: rgba(214, 175, 55, 0.94); }
      .agent-autofill-status[data-state="offline"]::before,
      .agent-autofill-status[data-state="error"]::before { background: rgba(207, 91, 75, 0.94); }
      .agent-autofill-button {
        min-height: 34px;
        width: 100%;
        display: inline-flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        white-space: nowrap;
      }
      .agent-autofill-button__label,
      .agent-autofill-button__timer {
        display: inline-flex;
        align-items: center;
        min-width: 0;
      }
      .agent-autofill-button__label {
        flex: 1 1 auto;
      }
      .agent-autofill-button__timer {
        flex: 0 0 auto;
        color: rgba(238, 247, 230, 0.88);
      }
      .agent-autofill-button[data-state="active"] {
        border-color: rgba(115, 182, 107, 0.54);
        background: rgba(52, 88, 48, 0.22);
        color: #eef7e6;
      }
      .agent-autofill-button[data-state="inactive"][disabled] {
        cursor: default;
        opacity: 0.72;
      }
      .agent-autofill-gear {
        min-width: 30px;
        width: 30px;
        height: 30px;
        padding: 0;
        border: 1px solid rgba(116, 126, 106, 0.2);
        background: rgba(255, 255, 255, 0.02);
        color: var(--text-soft);
        font-family: var(--mono);
        font-size: 14px;
        line-height: 1;
      }
      .agent-autofill-gear:hover {
        border-color: rgba(167, 178, 132, 0.42);
        background: rgba(167, 178, 132, 0.08);
        color: var(--text);
      }
      .agent-autofill-gear[data-open="true"] {
        border-color: rgba(115, 182, 107, 0.38);
        background: rgba(52, 88, 48, 0.18);
        color: #eef7e6;
      }
      .agent-autofill-prompt[hidden] {
        display: none !important;
      }
      .agent-autofill-prompt {
        display: grid;
        gap: 7px;
        padding-top: 4px;
        border-top: 1px solid rgba(116, 126, 106, 0.12);
      }
      .agent-autofill-prompt textarea {
        min-height: 72px;
        height: 72px;
        max-height: 148px;
        resize: vertical;
      }
      .agent-autofill-prompt-actions {
        display: flex;
        justify-content: space-between;
        gap: 8px;
      }
      .agent-autofill-prompt-actions .btn {
        min-width: 108px;
      }
      .agent-actions-row .btn {
        min-width: 136px;
      }
      .agent-result {
        flex: 1 1 auto;
        min-height: 320px;
        padding: 0;
        border: 1px solid rgba(116, 126, 106, 0.2);
        background: #0d120f;
        color: #d9ded4;
        white-space: normal;
        line-height: 1.45;
        overflow: auto;
        user-select: text;
      }
      .agent-result[data-state="empty"] {
        color: var(--muted);
      }
      .agent-result[data-state="error"] {
        border-color: rgba(151, 92, 83, 0.38);
        color: #f1d0c7;
      }
      .agent-result[data-tone="warning"] {
        border-color: rgba(167, 146, 92, 0.34);
      }
      .agent-result[data-tone="error"] {
        border-color: rgba(151, 92, 83, 0.38);
      }
      .agent-result[data-tone="success"] {
        border-color: rgba(116, 146, 106, 0.26);
      }
      .agent-result__fallback {
        white-space: pre-wrap;
        padding: 11px 12px;
      }
      .agent-console {
        display: grid;
        gap: 0;
        min-height: 320px;
        font-family: var(--mono);
        font-size: 11px;
      }
      .agent-console__entry {
        display: grid;
        gap: 4px;
        padding: 9px 12px;
        border-bottom: 1px solid rgba(116, 126, 106, 0.12);
      }
      .agent-console__entry:last-child {
        border-bottom: 0;
      }
      .agent-console__top {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
      }
      .agent-console__meta {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        min-width: 0;
      }
      .agent-console__level {
        display: inline-flex;
        align-items: center;
        padding: 1px 6px;
        border: 1px solid rgba(116, 126, 106, 0.24);
        color: var(--text-soft);
      }
      .agent-console__level[data-level="RUN"] {
        border-color: rgba(120, 178, 104, 0.42);
        color: #dff0d7;
      }
      .agent-console__level[data-level="INFO"] {
        border-color: rgba(122, 141, 168, 0.34);
        color: #cfd8e4;
      }
      .agent-console__level[data-level="WAIT"] {
        border-color: rgba(174, 153, 95, 0.38);
        color: #eadfae;
      }
      .agent-console__level[data-level="DONE"] {
        border-color: rgba(110, 156, 117, 0.36);
        color: #cee5c8;
      }
      .agent-console__level[data-level="WARN"] {
        border-color: rgba(169, 96, 86, 0.42);
        color: #efc0b7;
      }
      .agent-console__timestamp {
        color: rgba(194, 200, 188, 0.66);
        white-space: nowrap;
      }
      .agent-console__message {
        color: #f1f3ec;
        white-space: pre-wrap;
        word-break: break-word;
      }
      .agent-console__detail {
        color: rgba(194, 200, 188, 0.72);
        white-space: pre-wrap;
        word-break: break-word;
      }
      .agent-console__actions {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
      }
      .agent-console__action {
        min-height: 24px;
        padding: 4px 8px;
        border: 1px solid rgba(116, 126, 106, 0.22);
        background: rgba(255, 255, 255, 0.02);
        color: var(--text-soft);
        font-family: var(--mono);
        font-size: 10px;
        letter-spacing: 0.06em;
        text-transform: uppercase;
      }
      .agent-console__action:hover {
        border-color: rgba(167, 178, 132, 0.42);
        background: rgba(167, 178, 132, 0.08);
        color: var(--text);
      }
      .agent-result__hero {
        display: grid;
        gap: 6px;
      }
      .agent-result__hero-line {
        display: flex;
        align-items: flex-start;
        gap: 8px;
      }
      .agent-result__emoji {
        font-size: 18px;
        line-height: 1.1;
      }
      .agent-result__title {
        font-size: 17px;
        line-height: 1.2;
        font-weight: 700;
      }
      .agent-result__summary {
        color: var(--text-soft);
      }
      .agent-result__sections {
        display: grid;
        gap: 12px;
        margin-top: 12px;
      }
      .agent-result__section {
        display: grid;
        gap: 6px;
      }
      .agent-result__section-title {
        font-family: var(--mono);
        font-size: 10px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--text-soft);
      }
      .agent-result__section-body {
        color: var(--text);
      }
      .agent-result__list {
        display: grid;
        gap: 5px;
        margin: 0;
        padding: 0 0 0 18px;
      }
      .agent-result__list li::marker {
        color: rgba(167, 178, 132, 0.92);
      }
      .agent-result__actions {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        margin-top: 14px;
        padding-top: 10px;
        border-top: 1px solid rgba(116, 126, 106, 0.16);
      }
      .agent-result__action {
        min-height: 24px;
        padding: 4px 8px;
        border: 1px solid rgba(116, 126, 106, 0.22);
        background: rgba(0, 0, 0, 0.08);
        color: var(--text-soft);
        cursor: pointer;
        font-family: var(--mono);
        font-size: 9.5px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
      }
      .agent-result__action:hover {
        color: var(--text);
        border-color: rgba(167, 178, 132, 0.42);
        background: rgba(167, 178, 132, 0.08);
      }
      .agent-runs {
        display: grid;
        gap: 4px;
      }
      .agent-runs__list {
        display: grid;
        gap: 4px;
        padding: 0 10px 10px;
      }
      .agent-run-row {
        width: 100%;
        padding: 6px 8px;
        border: 1px solid rgba(116, 126, 106, 0.18);
        background: rgba(0, 0, 0, 0.06);
        color: var(--text);
        cursor: pointer;
        text-align: left;
        display: grid;
        gap: 2px;
      }
      .agent-run-row:hover {
        border-color: rgba(167, 178, 132, 0.42);
        background: rgba(167, 178, 132, 0.07);
      }
      .agent-run-row[data-active="true"] {
        border-color: rgba(167, 178, 132, 0.46);
        background: rgba(167, 178, 132, 0.1);
      }
      .agent-run-row__top {
        display: flex;
        justify-content: space-between;
        gap: 8px;
        align-items: center;
        font-family: var(--mono);
        font-size: 9.5px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
      }
      .agent-run-row__status {
        color: var(--text-soft);
      }
      .agent-run-row__summary {
        line-height: 1.3;
        display: -webkit-box;
        -webkit-box-orient: vertical;
        -webkit-line-clamp: 2;
        overflow: hidden;
      }
      .agent-run-row__meta {
        color: var(--muted);
        font-size: 10.5px;
      }
      .agent-details {
        border: 1px solid rgba(116, 126, 106, 0.18);
        background: rgba(0, 0, 0, 0.06);
      }
      .agent-details summary {
        padding: 8px 10px;
        cursor: pointer;
        font-family: var(--mono);
        font-size: 11px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--text-soft);
      }
      .agent-details--secondary summary {
        padding: 7px 10px;
      }
      .agent-actions-list {
        display: grid;
        gap: 6px;
        padding: 0 10px 10px;
      }
      .dialog--agent-tasks {
        width: min(1180px, calc(100% - 28px));
        max-height: min(86vh, 860px);
        padding: 0;
        gap: 0;
        overflow: hidden;
        display: flex;
        flex-direction: column;
      }
      .dialog--agent-tasks .dialog__head {
        padding: 11px 12px 9px;
        margin: 0;
        border-bottom: 1px solid rgba(115, 126, 105, 0.18);
        background: rgba(0, 0, 0, 0.08);
      }
      .agent-tasks-shell {
        flex: 1 1 auto;
        min-height: 0;
        overflow: auto;
        padding: 12px;
        display: grid;
        grid-template-columns: 360px minmax(0, 1fr);
        gap: 12px;
      }
      .agent-tasks-rail,
      .agent-tasks-editor {
        min-height: 0;
        display: grid;
        gap: 12px;
        align-content: start;
      }
      .agent-tasks-rail {
        grid-template-rows: auto minmax(0, 1fr);
      }
      .agent-tasks-toolbar {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        gap: 10px;
        flex-wrap: wrap;
      }
      .agent-tasks-toolbar__copy {
        display: grid;
        gap: 6px;
      }
      .agent-tasks-list {
        display: grid;
        align-content: start;
        gap: 8px;
        min-height: 220px;
        overflow: auto;
        padding-right: 2px;
      }
      .agent-tasks-empty {
        min-height: 220px;
        border: 1px dashed rgba(116, 128, 111, 0.28);
        background: rgba(0, 0, 0, 0.05);
        display: grid;
        align-content: center;
        gap: 8px;
        padding: 18px;
      }
      .agent-tasks-empty__title {
        font-family: var(--mono);
        font-size: 13px;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: var(--text-strong);
      }
      .agent-tasks-empty__text {
        color: var(--muted);
        line-height: 1.5;
      }
      .agent-task-row {
        display: grid;
        gap: 10px;
        padding: 12px;
        border: 1px solid var(--line);
        background: rgba(24, 31, 25, 0.92);
        cursor: pointer;
      }
      .agent-task-row:hover {
        border-color: rgba(182, 177, 116, 0.44);
        background: rgba(31, 38, 31, 0.94);
      }
      .agent-task-row[data-active="true"] {
        border-color: rgba(182, 177, 116, 0.6);
        box-shadow: inset 0 0 0 1px rgba(182, 177, 116, 0.12);
      }
      .agent-task-row[data-busy="true"] {
        border-color: rgba(116, 146, 106, 0.34);
      }
      .agent-task-row__top,
      .agent-task-row__footer {
        display: flex;
        justify-content: space-between;
        gap: 10px;
        align-items: flex-start;
        flex-wrap: wrap;
      }
      .agent-task-row__main {
        min-width: 0;
        display: grid;
        gap: 4px;
      }
      .agent-task-row__title {
        font-size: 13px;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--text-strong);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .agent-task-row__meta {
        font-size: 10.5px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--muted);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .agent-task-row__prompt {
        color: var(--text-soft);
        line-height: 1.45;
      }
      .agent-task-row__chips {
        display: flex;
        gap: 6px;
        flex-wrap: wrap;
      }
      .agent-task-row__timing {
        color: var(--muted);
        font-size: 11px;
        letter-spacing: 0.06em;
        text-transform: uppercase;
      }
      .agent-task-row__warning {
        padding: 8px 9px;
        border: 1px solid rgba(151, 92, 83, 0.26);
        background: rgba(63, 36, 32, 0.2);
        color: #e5b8ae;
        font-size: 11px;
        line-height: 1.45;
      }
      .agent-task-chip {
        min-width: 64px;
        padding: 7px 9px;
        border: 1px solid rgba(116, 128, 111, 0.34);
        font-size: 10.5px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        text-align: center;
        color: var(--muted-strong);
        background: rgba(24, 31, 25, 0.88);
      }
      .agent-task-chip[data-status="active"] {
        color: #b9d3b2;
        border-color: rgba(115, 182, 107, 0.4);
        background: rgba(47, 77, 45, 0.24);
      }
      .agent-task-chip[data-status="paused"] {
        color: #d3c9aa;
        border-color: rgba(176, 157, 101, 0.34);
        background: rgba(73, 65, 39, 0.18);
      }
      .agent-task-actions {
        display: flex;
        gap: 6px;
        flex-wrap: wrap;
      }
      .agent-task-actions .btn {
        min-width: 36px;
        padding: 6px 8px;
      }
      .agent-tasks-editor__head {
        display: grid;
        gap: 6px;
      }
      .agent-tasks-editor .field textarea {
        min-height: 136px;
        resize: vertical;
      }
      .agent-tasks-editor__row {
        display: grid;
        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
        gap: 10px;
      }
      .agent-tasks-editor__row--schedule {
        grid-template-columns: 140px 110px minmax(0, 1fr);
        align-items: end;
      }
      .agent-tasks-editor__foot {
        display: flex;
        justify-content: space-between;
        gap: 10px;
        flex-wrap: wrap;
        align-items: center;
      }
      .agent-tasks-editor__actions {
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
      }
      .agent-tasks-toggle {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        min-height: 34px;
        padding: 0 2px;
        font-size: 12px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--muted-strong);
      }
      .agent-tasks-meta {
        font-size: 11px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--muted);
        line-height: 1.45;
      }
      .agent-action-row {
        padding: 8px 9px;
        border: 1px solid rgba(116, 126, 106, 0.16);
        background: rgba(0, 0, 0, 0.08);
      }
      .agent-action-row__tool {
        font-family: var(--mono);
        font-size: 11px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--text-soft);
      }
      .agent-action-row__reason {
        margin-top: 2px;
        line-height: 1.35;
      }
      .agent-action-row__meta {
        margin-top: 4px;
        color: var(--muted);
        font-size: 12px;
      }
    .dialog--repair-order-payments .dialog__head {
      padding: 11px 12px 8px;
      margin: 0;
      border-bottom: 1px solid rgba(115, 126, 105, 0.18);
      background: rgba(0, 0, 0, 0.08);
    }
    .repair-order-payments-layout {
      display: grid;
      gap: 8px;
      min-height: 0;
      padding: 11px 12px 12px;
      overflow: auto;
    }
    .repair-order-payments-head {
      display: grid;
      gap: 6px;
    }
    .repair-order-payments-summary {
      display: grid;
      gap: 6px;
    }
    .repair-order-payments-stats {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 6px;
    }
    .repair-order-payments-stat {
      display: grid;
      gap: 2px;
      padding: 7px 8px;
      border: 1px solid rgba(116, 126, 106, 0.18);
      background: rgba(0, 0, 0, 0.08);
    }
    .repair-order-payments-stat span {
      color: var(--text-soft);
      font-family: var(--mono);
      font-size: 10px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .repair-order-payments-stat strong {
      color: var(--text);
      font-size: 16px;
      line-height: 1.1;
      font-variant-numeric: tabular-nums;
    }
    .repair-order-payments-subline {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.4;
    }
    .repair-order-payments-form {
      display: grid;
      grid-template-columns: minmax(132px, 154px) minmax(136px, 152px) auto;
      gap: 6px 7px;
      align-items: end;
      padding: 8px 9px;
      border: 1px solid rgba(116, 126, 106, 0.18);
      background: rgba(0, 0, 0, 0.08);
    }
    .repair-order-payments-form__note {
      grid-column: 1 / -1;
    }
    .repair-order-payments-form .field--compact input[type="text"],
    .repair-order-payments-form .field--compact select {
      min-height: 31px;
      padding: 5px 7px;
    }
    .repair-order-payments-form .btn {
      min-width: 104px;
      min-height: 31px;
    }
    .repair-order-payments-list {
      display: flex;
      flex-direction: column;
      gap: 5px;
      max-height: 288px;
      overflow: auto;
      padding: 4px 2px 0 0;
    }
    .repair-order-payment-row {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto auto;
      gap: 7px;
      align-items: center;
      padding: 6px 8px;
      border: 1px solid var(--line-soft);
      background: rgba(255,255,255,0.02);
    }
    .repair-order-payment-row__badge {
      min-width: 68px;
      padding: 3px 6px;
      border: 1px solid rgba(140, 151, 109, 0.28);
      text-align: center;
      color: var(--text-soft);
      font-family: var(--mono);
      font-size: 10px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .repair-order-payment-row__body {
      display: grid;
      gap: 2px;
      min-width: 0;
    }
    .repair-order-payment-row__line {
      color: var(--text);
      font-size: 12px;
      line-height: 1.3;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .repair-order-payment-row__subline {
      color: var(--muted);
      font-size: 11px;
      line-height: 1.3;
      white-space: normal;
      word-break: break-word;
    }
    .repair-order-payment-row__amount {
      font-size: 14px;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }
    .repair-order-payment-row__remove {
      min-width: 30px !important;
      width: 30px;
      padding: 0;
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
      gap: 6px;
      padding-top: 7px;
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
      gap: 5px;
      align-items: start;
    }
    .vehicle-group--identity .vehicle-group__grid {
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }
    .vehicle-field {
      gap: 4px;
    }
    .vehicle-field input,
    .vehicle-field select {
      min-height: 32px;
      padding: 5px 8px;
      font-size: 12.5px;
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
      min-height: 20px;
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
      gap: 5px;
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
        minmax(56px, 72px)
        minmax(132px, 160px)
        minmax(92px, 108px)
        minmax(108px, 124px)
        minmax(140px, 176px)
        minmax(124px, 146px)
        minmax(150px, 188px)
        minmax(320px, 2.8fr)
        minmax(88px, 104px)
        minmax(88px, 104px);
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
      padding: 9px 10px 8px;
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
      padding: 6px 10px;
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
    .repair-orders-row__phone,
    .repair-orders-row__vehicle,
    .repair-orders-row__title,
    .repair-orders-row__total {
      min-width: 0;
      font-family: var(--mono);
      font-size: 12px;
      line-height: 1.28;
    }
    .repair-orders-row__number {
      color: var(--text);
      font-weight: 700;
      white-space: nowrap;
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
    .repair-orders-row__dates {
      display: grid;
      gap: 4px;
      align-content: center;
      min-width: 0;
    }
    .repair-orders-row__date-meta {
      color: var(--text-soft);
      font-family: var(--mono);
      font-size: 10px;
      line-height: 1.2;
      letter-spacing: 0.04em;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .repair-orders-row__client,
    .repair-orders-row__phone,
    .repair-orders-row__vehicle,
    .repair-orders-row__title {
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .repair-orders-row__client,
    .repair-orders-row__phone,
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
      max-width: 100%;
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
    .repair-orders-row__payment-cell,
    .repair-orders-row__paid-cell,
    .repair-orders-row__total-cell {
      text-align: right;
    }
    .repair-orders-row__payment-cell {
      justify-items: start;
      text-align: left;
    }
    .repair-orders-row__paid-cell,
    .repair-orders-row__total-cell {
      justify-items: end;
    }
    .repair-orders-row__payment-status {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 24px;
      padding: 3px 8px;
      border: 1px solid rgba(167, 178, 132, 0.32);
      background: rgba(0, 0, 0, 0.12);
      color: var(--text-soft);
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      white-space: nowrap;
    }
    .repair-orders-row__payment-status[data-payment-status="paid"] {
      border-color: rgba(144, 198, 126, 0.4);
      color: #e9f5da;
      background: rgba(96, 134, 76, 0.2);
    }
    .repair-orders-row__payment-status[data-payment-status="unpaid"] {
      border-color: rgba(198, 170, 126, 0.34);
      color: #f1e8cf;
      background: rgba(109, 83, 40, 0.18);
    }
    .repair-orders-row__paid,
    .repair-orders-row__total {
      color: #f0ecdc;
      font-size: 13px;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }
    .repair-orders-row__paid[data-empty="true"],
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
      .repair-orders-row__payment-cell,
      .repair-orders-row__paid-cell,
      .repair-orders-row__total-cell {
        justify-items: start;
        text-align: left;
      }
    }
    @media (max-width: 760px) {
      .dialog--repair-orders {
        --repair-orders-columns: repeat(2, minmax(0, 1fr));
      }
      .repair-orders-row__payment-cell,
      .repair-orders-row__paid-cell,
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
      .repair-orders-row__payment-cell,
      .repair-orders-row__paid-cell,
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
      grid-template-columns: minmax(320px, 1fr) repeat(2, minmax(156px, 188px));
      gap: 8px 10px;
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
      gap: 7px;
    }
    .repair-order-tags-card .tag-list {
      min-height: 24px;
    }
    .repair-order-tag-list {
      gap: 4px;
    }
    .repair-order-tag-editor {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 8px;
      align-items: center;
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
      .dialog--card { width: min(1080px, 100%); }
      .dialog--repair-order { width: min(1260px, 100%); }
      .repair-order-groups { grid-template-columns: 1fr; }
      .repair-order-card__grid { grid-template-columns: 1fr; }
      .repair-order-footer { align-items: stretch; }
      .repair-order-footer__actions { width: 100%; }
      .repair-order-footer__actions .btn { flex: 1 1 0; min-width: 0; }
      .repair-order-payments-stats { grid-template-columns: 1fr; }
      .repair-order-payments-form { grid-template-columns: 1fr; }
      .repair-order-payments-form__note { grid-column: auto; }
      .cashbox-form-grid { grid-template-columns: 1fr; }
      .repair-order-payment-row { grid-template-columns: 1fr auto; align-items: start; }
      .repair-order-payment-row__badge { grid-column: 1 / -1; justify-self: start; }
      .repair-order-payment-row__remove { width: 100%; }
      .agent-tasks-shell,
      .agent-tasks-editor__row,
      .agent-tasks-editor__row--schedule { grid-template-columns: 1fr; }
      .agent-actions-row { grid-template-columns: 1fr; }
      .agent-actions-row .btn,
      .agent-tasks-launch { width: 100%; }
      .agent-task-row__top,
      .agent-task-row__footer { flex-direction: column; align-items: stretch; }
      .agent-task-actions { justify-content: flex-start; }
      .cashboxes-layout { grid-template-columns: 1fr; }
      .cashboxes-create-row { grid-template-columns: 1fr; }
      .cashbox-stats { grid-template-columns: 1fr; }
      .employees-layout { grid-template-columns: 1fr; }
      .employees-form-grid { grid-template-columns: repeat(6, minmax(0, 1fr)); }
      .employees-field--mode { grid-column: span 6; width: 100%; }
      .employees-field--salary,
      .employees-field--percent,
      .employees-field--active {
        grid-column: span 2;
        width: 100%;
      }
      .employees-field--active { padding-top: 0; }
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
    #cashboxesModal .dialog { width: min(1060px, 100%); }
    #cashboxTransferModal .dialog { width: min(820px, 100%); }
    .cashboxes-layout {
      display: grid;
      grid-template-columns: minmax(214px, 244px) minmax(0, 1fr);
      gap: 12px;
      min-height: 472px;
    }
    .cashboxes-pane {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .cashboxes-actions {
      display: flex;
      align-items: center;
      justify-content: flex-start;
      gap: 6px;
      flex-wrap: wrap;
    }
    .cashboxes-actions .btn {
      min-height: 34px;
      padding: 7px 10px;
    }
    .cashboxes-list {
      display: flex;
      flex-direction: column;
      gap: 6px;
      flex: 1 1 auto;
      max-height: 540px;
      overflow: auto;
      padding-right: 3px;
    }
    .cashbox-row {
      display: flex;
      flex-direction: column;
      gap: 3px;
      width: 100%;
      text-align: left;
      padding: 9px 10px;
      border: 1px solid var(--line-soft);
      background: rgba(255,255,255,0.02);
      color: var(--text);
      cursor: pointer;
    }
    .cashbox-row.is-active { border-color: rgba(167, 178, 132, 0.64); background: rgba(167, 178, 132, 0.06); box-shadow: inset 3px 0 0 rgba(167, 178, 132, 0.76); }
    .cashbox-row__head,
    .cashbox-stat-grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 6px;
      align-items: center;
    }
    .cashbox-row__name { font-size: 15px; font-weight: 700; line-height: 1.18; }
    .cashbox-row__balance,
    .cashbox-stat-grid__value {
      font-size: 16px;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }
    .cashbox-row__balance[data-balance-sign="negative"],
    .cashbox-stat-grid__value[data-balance-sign="negative"] { color: #f0b1a6; }
    .cashbox-transaction__meta,
    .cashboxes-empty {
      color: var(--muted);
      font-size: 11px;
      line-height: 1.35;
    }
    .cashbox-stats {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 6px;
    }
    .cashbox-stat-grid {
      border: 1px solid var(--line-soft);
      padding: 8px 9px;
      background: rgba(255,255,255,0.02);
    }
    .cashbox-stat-grid:first-child {
      border-color: rgba(167, 178, 132, 0.3);
      background: rgba(167, 178, 132, 0.06);
    }
    .cashbox-stat-grid__label {
      color: var(--muted);
      font-size: 10px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .cashbox-detail {
      display: flex;
      flex-direction: column;
      gap: 7px;
      min-width: 0;
    }
    .cashbox-detail__identity {
      display: grid;
      gap: 2px;
      min-width: 0;
    }
    .cashbox-composer {
      display: grid;
      gap: 7px;
      padding: 8px;
      border: 1px solid var(--line-soft);
      background: rgba(255,255,255,0.02);
    }
    .cashbox-composer__row {
      display: grid;
      grid-template-columns: minmax(144px, 166px) minmax(0, 1fr);
      gap: 8px;
      align-items: end;
    }
    .cashbox-composer__row .field--compact input[type="text"],
    .cashbox-composer__row .field--compact textarea {
      min-height: 34px;
      padding: 6px 8px;
    }
    .cashbox-composer textarea {
      min-height: 54px;
      max-height: 72px;
    }
    .cashbox-composer__actions {
      display: flex;
      justify-content: flex-end;
      gap: 6px;
      flex-wrap: wrap;
    }
    .cashbox-composer__actions .btn {
      min-height: 34px;
      padding: 7px 10px;
    }
    .cashbox-transfer-grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(248px, 290px);
      gap: 12px;
      align-items: start;
    }
    .cashbox-transfer-pane {
      display: grid;
      gap: 8px;
    }
    .cashbox-transfer-source {
      display: grid;
      gap: 3px;
      padding: 10px 12px;
      border: 1px solid var(--line-soft);
      background: rgba(255,255,255,0.02);
    }
    .cashbox-transfer-source__name {
      font-size: 17px;
      font-weight: 700;
      line-height: 1.2;
    }
    .cashbox-transfer-targets {
      display: grid;
      gap: 6px;
      max-height: 320px;
      overflow: auto;
      padding-right: 3px;
    }
    .cashbox-transfer-target {
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: 0;
      align-items: center;
      width: 100%;
      padding: 9px 10px;
      border: 1px solid var(--line-soft);
      background: rgba(255,255,255,0.02);
      color: var(--text);
      text-align: left;
      cursor: pointer;
    }
    .cashbox-transfer-target.is-active {
      border-color: rgba(167, 178, 132, 0.64);
      background: rgba(167, 178, 132, 0.08);
      box-shadow: inset 3px 0 0 rgba(167, 178, 132, 0.76);
    }
    .cashbox-transfer-target__name {
      font-size: 14px;
      font-weight: 700;
      line-height: 1.2;
    }
    .cashbox-transfer-summary {
      display: grid;
      gap: 6px;
      padding: 8px 9px;
      border: 1px solid var(--line-soft);
      background: rgba(255,255,255,0.02);
    }
    .cashbox-transfer-summary .field--compact input[type="text"],
    .cashbox-transfer-summary .field--compact textarea {
      min-height: 34px;
      padding: 6px 8px;
    }
    .cashbox-transfer-summary textarea {
      min-height: 74px;
      max-height: 112px;
    }
    .cashbox-transfer-foot {
      display: flex;
      justify-content: flex-end;
      gap: 6px;
      flex-wrap: wrap;
    }
    .cashbox-transfer-foot .btn {
      min-height: 34px;
      padding: 7px 10px;
    }
    .cashbox-transactions-card {
      display: grid;
      gap: 6px;
      padding: 9px;
      border: 1px solid var(--line-soft);
      background: rgba(255,255,255,0.02);
    }
    .cashbox-transactions-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      flex-wrap: wrap;
    }
    .cashbox-detail__head {
      display: flex;
      align-items: flex-start;
      gap: 10px;
    }
    .cashbox-delete-button {
      border-color: rgba(135, 113, 109, 0.34);
      color: rgba(224, 214, 208, 0.78);
      background: rgba(255,255,255,0.02);
    }
    .cashbox-delete-button:hover {
      border-color: rgba(163, 132, 126, 0.48);
      background: rgba(255,255,255,0.04);
      color: rgba(255, 228, 219, 0.92);
    }
    .cashbox-delete-button[disabled] {
      opacity: 0.42;
      cursor: not-allowed;
    }
    .cashbox-transactions {
      display: flex;
      flex-direction: column;
      gap: 5px;
      max-height: 340px;
      overflow: auto;
      padding-right: 3px;
    }
    .cashbox-transaction {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      gap: 6px;
      align-items: start;
      border: 1px solid var(--line-soft);
      padding: 6px 7px;
      background: rgba(255,255,255,0.02);
    }
    .cashbox-transaction__body {
      display: grid;
      gap: 3px;
      min-width: 0;
    }
    .cashbox-transaction__summary {
      display: flex;
      align-items: start;
      justify-content: space-between;
      gap: 8px;
    }
    .cashbox-transaction__badge {
      min-width: 58px;
      text-align: center;
      padding: 3px 6px;
      border: 1px solid var(--line-soft);
      font-size: 9.5px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .cashbox-transaction__badge[data-direction="income"] { border-color: rgba(67, 126, 79, 0.62); color: #d3efd9; }
    .cashbox-transaction__badge[data-direction="expense"] { border-color: rgba(152, 86, 78, 0.58); color: #ffd2c9; }
    .cashbox-transaction__amount {
      font-size: 14px;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }
    .cashbox-transaction__amount[data-direction="expense"] { color: #f0b1a6; }
    .cashbox-transaction__context {
      color: rgba(231, 226, 193, 0.72);
      font-size: 10px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      white-space: nowrap;
    }
    .cashbox-transaction__note {
      font-size: 12.5px;
      line-height: 1.3;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .employees-layout {
      display: grid;
      grid-template-columns: minmax(280px, 320px) minmax(0, 1fr);
      gap: 14px;
      align-items: start;
    }
    .employees-pane {
      display: grid;
      gap: 10px;
      min-height: 0;
    }
    .employees-pane--list {
      align-content: start;
      align-self: start;
      position: sticky;
      top: 0;
    }
    .employees-list-tools {
      display: grid;
      gap: 6px;
      margin-bottom: 8px;
    }
    .employees-search {
      width: 100%;
      min-height: 34px;
      padding: 7px 9px;
    }
    .employees-filterbar {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }
    .employees-filterbar .btn {
      min-height: 30px;
      padding: 6px 9px;
      font-size: 10.5px;
    }
    .employees-filterbar .btn.is-active {
      border-color: var(--accent);
      color: var(--accent);
      background: rgba(182, 177, 116, 0.08);
    }
    .employees-list-meta,
    .employees-report-meta,
    .employees-profile-meta {
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: rgba(231, 226, 193, 0.68);
      white-space: nowrap;
    }
    .employees-list {
      display: grid;
      gap: 6px;
      max-height: min(68vh, 640px);
      overflow: auto;
      padding-right: 4px;
    }
    .employees-row {
      border: 1px solid var(--line);
      background: rgba(21, 29, 23, 0.68);
      padding: 7px 9px;
      cursor: pointer;
      display: grid;
      gap: 3px;
      text-align: left;
    }
    .employees-row.is-active {
      border-color: var(--accent);
      background: rgba(38, 48, 40, 0.92);
      box-shadow: inset 0 0 0 1px rgba(182, 177, 116, 0.16);
    }
    .employees-row__top {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }
    .employees-row__title {
      font-size: 13px;
      font-weight: 700;
    }
    .employees-row__state {
      font-size: 10px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: rgba(231, 226, 193, 0.64);
      white-space: nowrap;
    }
    .employees-row__meta {
      font-size: 10.5px;
      opacity: 0.78;
      line-height: 1.3;
    }
    .employees-row__comp {
      font-size: 10.5px;
      color: rgba(231, 226, 193, 0.86);
      line-height: 1.25;
    }
    .employees-row__summary {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding-top: 3px;
      border-top: 1px solid rgba(164, 173, 138, 0.1);
      font-size: 10.5px;
      color: rgba(231, 226, 193, 0.76);
    }
    .employees-row__summary strong {
      font-size: 11.5px;
      color: var(--text);
    }
    .employees-actions {
      display: flex;
      gap: 6px;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
    }
    .employees-card-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
      margin-bottom: 10px;
    }
    .employees-card-head-main {
      display: grid;
      gap: 6px;
      min-width: 0;
      flex: 1 1 auto;
    }
    .employees-card-title {
      display: grid;
      gap: 4px;
    }
    .employees-card-title strong {
      font-size: 18px;
      line-height: 1.1;
      letter-spacing: 0.03em;
      text-transform: uppercase;
      color: var(--text);
    }
    .employees-card-actions {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 8px;
      flex-wrap: wrap;
    }
    .employees-card-mode {
      font-size: 11px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: rgba(231, 226, 193, 0.68);
    }
    .employees-form-grid {
      display: grid;
      grid-template-columns: repeat(12, minmax(0, 1fr));
      gap: 8px 10px;
      align-items: end;
    }
    .employees-form-grid .field,
    .employees-form-grid .employees-check {
      min-width: 0;
    }
    .employees-form-grid input[type="text"],
    .employees-form-grid select {
      min-height: 34px;
      padding: 7px 9px;
    }
    .employees-field--span-2 { grid-column: span 2; }
    .employees-field--span-4 { grid-column: span 4; }
    .employees-field--span-6 { grid-column: span 6; }
    .employees-field--span-12 { grid-column: 1 / -1; }
    .employees-field--compact {
      justify-self: start;
      width: min(100%, var(--employees-field-width, 100%));
    }
    .employees-field--mode {
      --employees-field-width: 320px;
    }
    .employees-field--salary {
      --employees-field-width: 176px;
    }
    .employees-field--percent {
      --employees-field-width: 148px;
    }
    .employees-field--active {
      justify-self: start;
      width: auto;
      padding-top: 20px;
    }
    .employees-form-grid .field--secondary {
      opacity: 0.72;
    }
    .employees-form-grid .field--wide {
      grid-column: 1 / -1;
    }
    .employees-note-details {
      margin-top: 8px;
      border: 1px solid rgba(164, 173, 138, 0.12);
      background: rgba(18, 24, 20, 0.34);
    }
    .employees-note-details summary {
      cursor: pointer;
      list-style: none;
      padding: 8px 10px;
      font-size: 11px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: rgba(231, 226, 193, 0.7);
    }
    .employees-note-details summary::-webkit-details-marker {
      display: none;
    }
    .employees-note-details[open] summary {
      border-bottom: 1px solid rgba(164, 173, 138, 0.1);
    }
    .employees-note-details .field {
      padding: 8px 10px;
    }
    .employees-check {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      opacity: 0.9;
      min-height: 34px;
    }
    .employees-panel-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      flex-wrap: wrap;
    }
    .employees-panel-head .repair-orders-search {
      width: 154px;
      min-height: 34px;
    }
    .employees-summary-strip {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
      margin-top: 10px;
    }
    .employees-summary-strip:empty {
      display: none;
    }
    .employees-kpi {
      display: grid;
      gap: 3px;
      padding: 9px 10px;
      border: 1px solid rgba(164, 173, 138, 0.12);
      background: rgba(18, 24, 20, 0.44);
    }
    .employees-kpi--accent {
      background: rgba(182, 177, 116, 0.09);
      border-color: rgba(182, 177, 116, 0.26);
      box-shadow: inset 0 0 0 1px rgba(182, 177, 116, 0.08);
    }
    .employees-kpi__label {
      font-size: 10px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: rgba(231, 226, 193, 0.68);
    }
    .employees-kpi__value {
      font-size: 14px;
      font-weight: 700;
      line-height: 1.2;
    }
    .employees-kpi--accent .employees-kpi__value {
      font-size: 19px;
    }
    .employees-report-shell {
      display: grid;
      gap: 10px;
    }
    .employees-report-tabs {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }
    .employees-report-tabs .btn {
      min-height: 30px;
      padding: 6px 10px;
      font-size: 10.5px;
    }
    .employees-report-tabs .btn.is-active {
      border-color: var(--accent);
      color: var(--accent);
      background: rgba(182, 177, 116, 0.08);
    }
    .employees-report-panel {
      display: none;
    }
    .employees-report-panel.is-active {
      display: block;
    }
    .employees-table-wrap {
      overflow: auto;
      max-height: 236px;
      border: 1px solid var(--line);
      background: rgba(18, 24, 20, 0.6);
    }
    .employees-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }
    .employees-table th,
    .employees-table td {
      padding: 7px 9px;
      border-bottom: 1px solid rgba(164, 173, 138, 0.1);
      text-align: left;
      vertical-align: top;
    }
    .employees-table th {
      position: sticky;
      top: 0;
      background: rgba(31, 39, 33, 0.98);
      z-index: 1;
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: rgba(231, 226, 193, 0.72);
    }
    .employees-table tr.is-active td {
      background: rgba(182, 177, 116, 0.08);
    }
    .employees-table tr[data-employee-id],
    .employees-table tr[data-card-id] {
      cursor: pointer;
    }
    .employees-table tr[data-employee-id]:hover td,
    .employees-table tr[data-card-id]:hover td {
      background: rgba(182, 177, 116, 0.06);
    }
    .employees-table td.is-num,
    .employees-table th.is-num {
      text-align: right;
      white-space: nowrap;
    }
    @media (max-width: 1160px) {
      .employees-layout {
        grid-template-columns: minmax(0, 1fr);
      }
      .employees-pane--list {
        position: static;
      }
    }
    @media (max-width: 760px) {
      .employees-summary-strip {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
      .employees-card-actions {
        width: 100%;
        justify-content: space-between;
      }
    }
    .repair-order-table__select {
      width: 100%;
      min-height: 36px;
      background: rgba(18, 24, 20, 0.84);
      border: 1px solid var(--line);
      color: var(--text);
      padding: 8px 10px;
      font: inherit;
      text-transform: uppercase;
      letter-spacing: 0.08em;
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
          <div class="brand__title">AUTOSTOP / ПУЛЬТ</div>
          <div class="topbar__meta">
            <div class="brand__sub">МИНИМУМ ИНТЕРФЕЙСА · ХОСТ В СЕТИ</div>
            <div class="status-shell" id="topbarStatusHost"></div>
          </div>
        </div>
      </div>
      <div class="topbar__actions">
        <button class="btn" id="operatorButton">ОПЕРАТОР</button>
        <button class="btn" id="archiveButton">АРХИВ</button>
        <button class="btn" id="repairOrdersButton">ЗАКАЗ-НАРЯДЫ</button>
        <button class="btn" id="cashboxesButton">КАССЫ</button>
        <button class="btn" id="employeesButton">СОТРУДНИКИ</button>
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
        <div>Даты</div>
        <div>Статус</div>
        <div>Оплата</div>
        <div>Клиент</div>
        <div>Телефон</div>
        <div>Автомобиль</div>
        <div>Смысл карточки</div>
        <div class="repair-orders-table-head__sum">Внесено</div>
        <div class="repair-orders-table-head__sum">Сумма</div>
      </div>
      <div class="repair-orders-list" id="repairOrdersList"></div>
    </div>
  </div>

  <div class="modal" id="cashboxesModal">
    <div class="dialog">
      <div class="dialog__head">
        <div class="dialog__title">КАССЫ</div>
        <button class="btn" data-close="cashboxes">ЗАКРЫТЬ</button>
      </div>
      <div class="cashboxes-layout">
        <div class="subpanel cashboxes-pane">
          <div class="panel-title">КАССЫ</div>
          <div class="cashboxes-actions">
            <button class="btn btn--accent" id="cashboxCreateButton">+ ДОБАВИТЬ</button>
            <button class="btn btn--ghost cashbox-delete-button" id="cashboxDeleteButton">- УДАЛИТЬ</button>
          </div>
          <div class="cashboxes-list" id="cashboxesList"></div>
        </div>
        <div class="subpanel cashbox-detail cashboxes-pane">
          <div class="cashbox-detail__head">
            <div class="cashbox-detail__identity">
              <div class="panel-title" id="cashboxDetailTitle">КАССА НЕ ВЫБРАНА</div>
              <div class="cashbox-detail__meta" id="cashboxDetailMeta"></div>
            </div>
          </div>
          <div class="cashbox-stats" id="cashboxStats"></div>
          <div class="cashbox-composer">
            <div class="cashbox-composer__row">
              <div class="field field--compact">
                <label for="cashboxAmountInput">СУММА</label>
                <input id="cashboxAmountInput" type="text" inputmode="decimal" maxlength="24">
              </div>
              <div class="field field--compact">
                <label for="cashboxNoteInput">КОММЕНТАРИЙ</label>
                <textarea id="cashboxNoteInput" maxlength="240" placeholder="Коротко опишите движение."></textarea>
              </div>
            </div>
            <div class="cashbox-composer__actions">
              <button class="btn btn--accent" id="cashboxIncomeButton">+ ПОСТУПЛЕНИЕ</button>
              <button class="btn" id="cashboxTransferButton">- ПЕРЕМЕЩЕНИЕ</button>
              <button class="btn" id="cashboxExpenseButton">- СПИСАНИЕ</button>
            </div>
          </div>
          <div class="cashbox-transactions-card">
            <div class="cashbox-transactions-head">
              <div class="panel-title">ДВИЖЕНИЯ</div>
            </div>
            <div class="cashbox-transactions" id="cashboxTransactions"></div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <div class="modal" id="cashboxTransferModal">
    <div class="dialog">
      <div class="dialog__head">
        <div class="dialog__title">ПЕРЕМЕЩЕНИЕ</div>
        <button class="btn" data-close="cashbox-transfer">ЗАКРЫТЬ</button>
      </div>
      <div class="cashbox-transfer-grid">
        <div class="cashbox-transfer-pane">
            <div class="cashbox-transfer-source">
              <div class="cashbox-transfer-source__name" id="cashboxTransferSourceName">КАССА НЕ ВЫБРАНА</div>
            </div>
          <div class="cashbox-transfer-summary">
            <div class="field field--compact">
              <label for="cashboxTransferAmountInput">СУММА</label>
                <input id="cashboxTransferAmountInput" type="text" inputmode="decimal" maxlength="24">
            </div>
            <div class="field field--compact">
              <label for="cashboxTransferNoteInput">КОММЕНТАРИЙ</label>
              <textarea id="cashboxTransferNoteInput" maxlength="240" placeholder="Коротко опишите перемещение."></textarea>
            </div>
          </div>
        </div>
        <div class="cashbox-transfer-pane">
          <div class="cashbox-transfer-targets" id="cashboxTransferTargets"></div>
        </div>
      </div>
      <div class="cashbox-transfer-foot">
        <button class="btn btn--ghost" data-close="cashbox-transfer">ОТМЕНА</button>
        <button class="btn btn--accent" id="cashboxTransferConfirmButton">ПЕРЕМЕСТИТЬ</button>
      </div>
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
        <button class="btn" id="cardModalCloseButtonTop" data-close="card" onclick="window.__closeCardModal && window.__closeCardModal(); return false;">ЗАКРЫТЬ</button>
      </div>
      <div class="dialog__tabs dialog__tabs--card">
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
                <label for="cardTitle">КРАТКАЯ СУТЬ</label>
                <input id="cardTitle" type="text" maxlength="120">
              </div>
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
                  <label class="signal-cell signal-cell--timer">
                    <span class="signal-cell__label">&#1044;&#1085;&#1077;&#1081;</span>
                    <div class="signal-stepper">
                       <button class="signal-stepper__button" id="signalDaysDecrementButton" type="button" aria-label="Уменьшить дни">&minus;</button>
                      <span class="signal-stepper__value" id="signalDaysDisplay">00</span>
                      <button class="signal-stepper__button" id="signalDaysIncrementButton" type="button" aria-label="Увеличить дни">+</button>
                      <span class="signal-stepper__unit">&#1076;</span>
                    </div>
                  </label>
                  <label class="signal-cell signal-cell--timer">
                    <span class="signal-cell__label">&#1063;&#1072;&#1089;&#1086;&#1074;</span>
                    <div class="signal-stepper">
                       <button class="signal-stepper__button" id="signalHoursDecrementButton" type="button" aria-label="Уменьшить часы">&minus;</button>
                      <span class="signal-stepper__value" id="signalHoursDisplay">00</span>
                      <button class="signal-stepper__button" id="signalHoursIncrementButton" type="button" aria-label="Увеличить часы">+</button>
                      <span class="signal-stepper__unit">&#1095;</span>
                    </div>
                  </label>
                  <input class="signal-input--hidden" id="signalDays" type="number" min="0" max="365">
                  <input class="signal-input--hidden" id="signalHours" type="number" min="0" max="23">
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
                  <div class="tag-controls">
                    <div class="tag-color-picker" id="tagColorPicker"></div>
                    <div class="tag-entry">
                      <input id="tagInput" type="text" maxlength="24" placeholder="ЖДЁМ">
                      <button class="btn" id="tagAddButton">+</button>
                    </div>
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
          <div class="file-dropzone" id="fileDropzone" tabindex="0" contenteditable="plaintext-only" spellcheck="false" data-title="ПЕРЕНЕСИТЕ ИЛИ ВСТАВЬТЕ ФАЙЛ" data-hint="Ctrl+V, правый клик -> Вставить, drag-and-drop или клик для выбора. PNG, JPG, JPEG, WEBP, GIF, TXT, PDF, Word, Excel." aria-label="Поле для вставки и переноса файлов"></div>
          <div class="file-dropzone__meta" id="fileDropMeta">Сначала сохраните карточку, затем добавляйте вложения.</div>
          <div class="file-upload-legacy" hidden>
          <input id="fileInput" type="file" multiple hidden accept=".png,.jpg,.jpeg,.webp,.gif,.txt,.pdf,.doc,.docx,.xls,.xlsx,image/png,image/jpeg,image/webp,image/gif,text/plain,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet">
            <button class="btn" id="uploadButton">ЗАГРУЗИТЬ</button>
          </div>
          <div id="fileList"></div>
        </div>
      </section>
      <section data-panel="journal" class="hidden">
        <div class="log-view" id="logList"></div>
      </section>
      <div class="dialog__foot dialog__foot--card">
        <div class="dialog__foot-group dialog__foot-group--danger">
          <button class="btn btn--danger hidden" id="archiveAction">В АРХИВ</button>
          <button class="btn hidden" id="restoreAction">ВЕРНУТЬ ИЗ АРХИВА</button>
        </div>
        <div class="dialog__foot-group dialog__foot-group--main">
          <button class="btn" id="cardModalCloseButtonBottom" data-close="card" onclick="window.__closeCardModal && window.__closeCardModal(); return false;">ОТМЕНА</button>
          <button class="btn btn--ghost card-agent-button" id="cardAgentButton" type="button" title="Прибраться в карточке" aria-label="Прибраться в карточке"></button>
          <button class="btn btn--accent" id="saveCardButton">СОХРАНИТЬ</button>
        </div>
      </div>
    </div>
  </div>

  <div class="modal" id="repairOrderModal">
    <div class="dialog dialog--repair-order">
      <div class="dialog__head dialog__head--card dialog__head--repair-order">
        <div class="dialog__title-wrap">
          <div class="repair-order-headline">
            <div class="dialog__title dialog__title--card" id="repairOrderModalTitle">ЗАКАЗ-НАРЯД</div>
            <div class="repair-order-status" id="repairOrderStatus">Открыт</div>
          </div>
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
            <div class="repair-order-hidden-fields" aria-hidden="true">
              <div class="field field--compact repair-order-field repair-order-field--payment-method">
                <label for="repairOrderPaymentMethod">ФОРМА ОПЛАТЫ</label>
                <select id="repairOrderPaymentMethod" data-repair-order-field="payment_method" tabindex="-1">
                  <option value="cash">Наличный</option>
                  <option value="cashless">Безналичный</option>
                </select>
              </div>
              <div class="field field--compact repair-order-field repair-order-field--prepayment">
                <label for="repairOrderPrepayment">ПРЕДОПЛАТА</label>
                <input id="repairOrderPrepayment" data-repair-order-field="prepayment" type="hidden" value="0">
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
          <div class="tag-list repair-order-tag-list" id="repairOrderTagList"></div>
          <div class="repair-order-tag-editor">
            <div class="tag-color-picker" id="repairOrderTagColorPicker"></div>
            <div class="tag-entry">
              <input id="repairOrderTagInput" type="text" maxlength="24" placeholder="МЕТКА">
              <button class="btn" id="repairOrderTagAddButton" type="button">+</button>
            </div>
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
                  <th style="width:40%;">Наименование</th>
                  <th style="width:18%;">Исполнитель</th>
                  <th class="repair-order-table__numeric" style="width:9%;">Кол-во</th>
                  <th class="repair-order-table__numeric" style="width:14%;">Цена</th>
                  <th class="repair-order-table__numeric" style="width:15%;">Сумма</th>
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
                  <th style="width:38%;">Наименование</th>
                  <th style="width:17%;">Кат. №</th>
                  <th class="repair-order-table__numeric" style="width:10%;">Кол-во</th>
                  <th class="repair-order-table__numeric" style="width:15%;">Цена</th>
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
            <div class="repair-order-total repair-order-total--cashless">
              <span>БЕЗНАЛ 15%</span>
              <strong data-repair-order-total="cashless">0,00</strong>
            </div>
            <div class="repair-order-total repair-order-total--cash">
              <span>НАЛИЧНЫЕ</span>
              <strong data-repair-order-total="subtotal">0,00</strong>
            </div>
            <div class="repair-order-total is-hidden" data-repair-order-total-block="taxes">
              <span>НАЛОГИ И СБОРЫ</span>
              <strong data-repair-order-total="taxes">0,00</strong>
            </div>
            <div class="repair-order-total repair-order-total--summary">
              <span>ИТОГО ПО ЗАКАЗ-НАРЯДУ</span>
              <strong data-repair-order-total="grand">0,00</strong>
            </div>
            <div class="repair-order-total repair-order-total--grand">
              <span>К ДОПЛАТЕ</span>
              <strong data-repair-order-total="due">0,00</strong>
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
    const BOARD_SCALE_STORAGE_KEY_PREFIX = 'kanban-board-scale:';
    const ARCHIVE_PREVIEW_LIMIT = 30;

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
      archiveCards: [],
      archiveLoaded: false,
      archiveLoading: null,
      lastSnapshotRevision: '',
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
      boardDragColumnId: '',
      boardDropColumnId: '',
      boardDropBeforeCardId: '',
      boardDropBeforeColumnId: '',
      unreadHoverTimers: new Map(),
      unreadSeenInFlight: new Set(),
      repairOrdersFilter: 'open',
      repairOrdersQuery: '',
      repairOrdersSortBy: 'opened_at',
      repairOrdersSortDir: 'desc',
      repairOrdersLoadTimer: null,
      cashboxes: [],
      activeCashboxId: '',
      activeCashbox: null,
      cashboxTransferDraft: {
        sourceId: '',
        targetId: '',
        amount: '',
        note: '',
      },
      employees: [],
      activeEmployeeId: '',
      employeeCreateMode: false,
      employeesReportTab: 'summary',
      employeesQuery: '',
      employeesVisibilityFilter: 'active',
      employeeFormBaseline: null,
      payrollMonth: '',
      payrollReport: null,
      employeesUiBound: false,
      repairOrderPaymentsUiBound: false,
      repairOrderTags: [],
      repairOrderPayments: [],
      repairOrderTagColor: 'green',
      agentUiBound: false,
      aiSurfaceUiBound: false,
      aiChatWindowUiBound: false,
      agentContext: { kind: 'board' },
      aiSurfaceContext: { kind: 'chat' },
      aiSurfaceSelectedScenario: 'ai_chat',
      aiCompactContext: { kind: 'compact_context' },
      aiCompactContextCache: { signature: '', packet: null },
      aiChatWindowContext: { kind: 'chat' },
      aiChatWindowHistory: [],
      aiChatWindowHistoryContext: null,
      aiChatWindowMessageSeq: 0,
      aiChatWindowSettingsOpen: false,
      aiChatWindowPromptProfile: {
        system_instruction: 'Чат отвечает как отдельный рабочий AI surface AutoStop CRM.',
        response_profile: 'Кратко, структурно, с читаемыми блоками и без лишнего шума.',
        user_tune: '',
      },
      aiChatWindowKnowledge: null,
      agentRefreshTimer: null,
      agentAutofillCountdownTimer: null,
      agentAutofillPromptOpen: false,
      agentTasksUiBound: false,
      agentTasksRefreshTimer: null,
      agentScheduledTasks: [],
      agentScheduledColumns: [],
      agentScheduledActiveId: '',
      agentTaskScopeCardId: '',
      agentTaskScopeCardLabel: '',
      agentTaskId: '',
      agentTaskStatus: '',
      agentSyncedTaskId: '',
      agentStatusPayload: null,
      aiSurfaceStatusPayload: null,
      aiChatWindowStatusPayload: null,
      agentLatestTasks: [],
      agentLatestActions: [],
      cardCleanupState: 'idle',
      cardCleanupError: '',
    };

    const SNAPSHOT_POLL_INTERVAL_MS = 8000;
    const SNAPSHOT_POLL_MODAL_INTERVAL_MS = 15000;
    const SNAPSHOT_POLL_HIDDEN_INTERVAL_MS = 60000;
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
          { name: 'display_name', label: 'Марка / модель', placeholder: 'Subaru Legacy', wide: true },
          { name: 'license_plate', label: 'Гос номер', placeholder: 'А123АА124', mono: true },
          { name: 'production_year', label: 'Год', type: 'number', min: '1900', max: '2100', step: '1', placeholder: '2016' },
          { name: 'mileage', label: 'Пробег', type: 'number', min: '0', step: '1', placeholder: '185000' },
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
      4,
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

    function ensureRepairOrderPaymentsUi() {
      const footerActions = document.querySelector('#repairOrderModal .repair-order-footer__actions');
      if (footerActions && !document.getElementById('repairOrderPaymentsButton')) {
        const button = document.createElement('button');
        button.className = 'btn btn--ghost repair-order-money-button';
        button.id = 'repairOrderPaymentsButton';
        button.type = 'button';
        button.textContent = '?';
        footerActions.insertBefore(button, document.getElementById('repairOrderPrintButton'));
      }
    }

    function ensureRepairOrderPaymentsModalUi() {
      if (!document.getElementById('repairOrderPaymentsModal')) {
        document.body.insertAdjacentHTML(
          'beforeend',
          '<div class="modal" id="repairOrderPaymentsModal">'
            + '<div class="dialog dialog--repair-order-payments">'
            + '<div class="dialog__head">'
            + '<div class="dialog__title">Оплаты по заказ-наряду</div>'
            + '<button class="btn" data-close="repair-order-payments">Закрыть</button>'
            + '</div>'
            + '<div class="repair-order-payments-layout">'
            + '<div class="repair-order-payments-head"><div class="repair-order-payments-summary" id="repairOrderPaymentsMeta">Пока нет оплат.</div></div>'
            + '<div class="repair-order-payments-form">'
            + '<div class="field field--compact"><label for="repairOrderPaymentCashbox">Касса</label><select id="repairOrderPaymentCashbox"><option value="">Выберите кассу</option></select></div>'
            + '<div class="field field--compact"><label for="repairOrderPaymentAmount">Сумма</label><input id="repairOrderPaymentAmount" type="text" inputmode="decimal" maxlength="24"></div>'
            + '<button class="btn btn--accent" id="repairOrderPaymentAddButton" type="button">+ Оплата</button>'
            + '<div class="field field--compact repair-order-payments-form__note"><label for="repairOrderPaymentNote">Комментарий</label><input id="repairOrderPaymentNote" type="text" maxlength="240" placeholder="Предоплата / оплата по работам"></div>'
            + '</div>'
            + '<div class="repair-order-payments-list" id="repairOrderPaymentsList"></div>'
            + '</div>'
            + '</div>'
            + '</div>'
        );
      }
    }
    function ensureAgentUi() {
      if (!document.getElementById('agentModal')) {
        document.body.insertAdjacentHTML(
          'beforeend',
          '<div class="modal" id="agentModal">'
            + '<div class="dialog dialog--agent">'
              + '<div class="dialog__head">'
                + '<div class="dialog__title">АГЕНТ</div>'
                + '<button class="btn" data-close="agent">ЗАКРЫТЬ</button>'
              + '</div>'
              + '<div class="agent-shell">'
                + '<div class="agent-headline">'
                  + '<div class="agent-context" id="agentContextLabel">КОНТЕКСТ: ДОСКА</div>'
                  + '<div class="agent-status" id="agentStatusLabel" data-state="idle">OFFLINE</div>'
                + '</div>'
                + '<div class="agent-shortcuts" id="agentQuickActions"></div>'
                + '<div class="field field--compact agent-field">'
                  + '<label for="agentTaskInput">ЗАПРОС</label>'
                  + '<textarea id="agentTaskInput" maxlength="1600" placeholder="Сделай обзор доски"></textarea>'
                + '</div>'
                + '<div class="agent-actions-row">'
                  + '<button class="btn agent-tasks-launch" id="agentTasksOpenButton" type="button">'
                    + '<span class="agent-tasks-launch__title">ЗАДАЧИ</span>'
                    + '<span class="agent-tasks-launch__meta">РАСПИСАНИЕ, ЗАПУСКИ И КОНТРОЛЬ</span>'
                  + '</button>'
                  + '<div class="agent-autofill-panel">'
                    + '<div class="agent-autofill-panel__top">'
                      + '<button class="btn btn--ghost agent-autofill-button" id="agentAutofillButton" type="button">АВТОЗАПОЛНЕНИЕ</button>'
                      + '<button class="agent-autofill-gear" id="agentAutofillPromptToggle" type="button" title="Mini-prompt" aria-label="Mini-prompt">?</button>'
                    + '</div>'
                    + '<div class="agent-autofill-status" id="agentAutofillStatus" data-state="offline">SERVER AI OFFLINE</div>'
                    + '<div class="agent-autofill-prompt" id="agentAutofillPromptPanel" hidden>'
                      + '<div class="field field--compact">'
                        + '<label for="agentAutofillPromptInput">MINI-PROMPT</label>'
                        + '<textarea id="agentAutofillPromptInput" maxlength="800" placeholder="Например: не переписывай цены и артикулы, добавляй только ИИ-комментарии для следующего мастера"></textarea>'
                      + '</div>'
                      + '<div class="agent-autofill-prompt-actions">'
                        + '<button class="btn btn--ghost" id="agentAutofillPromptResetButton" type="button">СБРОС</button>'
                        + '<button class="btn btn--accent" id="agentAutofillPromptSaveButton" type="button">СОХРАНИТЬ</button>'
                      + '</div>'
                    + '</div>'
                  + '</div>'
                  + '<button class="btn btn--accent" id="agentRunButton" type="button">ВЫПОЛНИТЬ</button>'
                + '</div>'
                + '<div class="agent-result" id="agentResultPanel" data-state="empty">Введите запрос.</div>'
                + '<details class="agent-details agent-details--secondary" id="agentRunsDetails">'
                  + '<summary>ПОСЛЕДНИЕ ЗАПУСКИ</summary>'
                  + '<div class="agent-runs__list" id="agentRunsList"><div class="cashboxes-empty">Запусков пока нет.</div></div>'
                + '</details>'
                + '<details class="agent-details" id="agentDetails">'
                  + '<summary>ДЕЙСТВИЯ</summary>'
                  + '<div class="agent-actions-list" id="agentActionsList"><div class="cashboxes-empty">Действий пока нет.</div></div>'
                + '</details>'
              + '</div>'
            + '</div>'
          + '</div>'
        );
      }
    }
    function ensureAgentTasksUi() {
      if (document.getElementById('agentTasksModal')) return;
      document.body.insertAdjacentHTML(
        'beforeend',
        '<div class="modal" id="agentTasksModal">'
          + '<div class="dialog dialog--agent-tasks">'
            + '<div class="dialog__head">'
              + '<div class="dialog__title">ЗАДАЧИ</div>'
              + '<button class="btn" data-close="agent-tasks">ЗАКРЫТЬ</button>'
            + '</div>'
            + '<div class="agent-tasks-shell">'
              + '<div class="subpanel agent-tasks-rail">'
                + '<div class="agent-tasks-toolbar">'
                  + '<div class="agent-tasks-toolbar__copy">'
                    + '<div class="panel-title">СПИСОК ЗАДАЧ</div>'
                    + '<div class="agent-tasks-meta" id="agentTasksMeta">ЗАГРУЗКА ЗАДАЧ...</div>'
                  + '</div>'
                  + '<button class="btn btn--accent" id="agentTasksNewButton" type="button">НОВАЯ ЗАДАЧА</button>'
                + '</div>'
                + '<div class="agent-tasks-list" id="agentTasksList"></div>'
              + '</div>'
              + '<div class="subpanel agent-tasks-editor">'
                + '<div class="agent-tasks-editor__head">'
                  + '<div class="panel-title" id="agentTasksEditorTitle">НОВАЯ ЗАДАЧА</div>'
                  + '<div class="agent-tasks-meta" id="agentTaskFormMeta">Текущая карточка, одна колонка или вся доска. Запуск вручную, по интервалу или при создании.</div>'
                + '</div>'
                + '<div class="field field--compact"><label for="agentTaskNameInput">НАЗВАНИЕ</label><input id="agentTaskNameInput" type="text" maxlength="80" placeholder="Проверка оплат"></div>'
                + '<div class="field field--compact"><label for="agentTaskPromptInput">ЗАДАЧА</label><textarea id="agentTaskPromptInput" maxlength="8000" placeholder="Например: проверь неоплаченные заказ-наряды и кратко запиши результат в карточки"></textarea></div>'
                + '<div class="agent-tasks-editor__row">'
                + '<div class="field field--compact"><label for="agentTaskScopeTypeInput">ОХВАТ</label><select id="agentTaskScopeTypeInput"><option value="all_cards">ВСЕ КАРТОЧКИ</option><option value="column">ОДНА КОЛОНКА</option><option value="current_card">ТЕКУЩАЯ КАРТОЧКА</option></select></div>'
                  + '<div class="field field--compact"><label for="agentTaskScopeColumnInput">КОЛОНКА</label><select id="agentTaskScopeColumnInput"></select></div>'
                + '</div>'
                + '<div class="agent-tasks-editor__row agent-tasks-editor__row--schedule">'
                  + '<div class="field field--compact"><label for="agentTaskScheduleTypeInput">РЕЖИМ</label><select id="agentTaskScheduleTypeInput"><option value="once">ОДИН РАЗ</option><option value="interval">ИНТЕРВАЛ</option><option value="on_create">ПРИ СОЗДАНИИ</option></select></div>'
                  + '<div class="field field--compact"><label for="agentTaskIntervalValueInput">ЧИСЛО</label><input id="agentTaskIntervalValueInput" type="number" min="1" max="999" step="1" value="1"></div>'
                  + '<div class="field field--compact"><label for="agentTaskIntervalUnitInput">ЕДИНИЦА</label><select id="agentTaskIntervalUnitInput"><option value="minute">МИН</option><option value="hour">ЧАС</option></select></div>'
                + '</div>'
                + '<label class="agent-tasks-toggle"><input id="agentTaskActiveInput" type="checkbox" checked> ЗАДАЧА АКТИВНА</label>'
                + '<div class="agent-tasks-editor__foot">'
                  + '<div class="agent-tasks-editor__actions">'
                    + '<button class="btn btn--accent" id="agentTaskSaveButton" type="button">СОХРАНИТЬ</button>'
                    + '<button class="btn btn--ghost" id="agentTaskRunButton" type="button">ЗАПУСТИТЬ</button>'
                  + '</div>'
                  + '<button class="btn btn--ghost" id="agentTaskResetButton" type="button">НОВАЯ</button>'
                + '</div>'
              + '</div>'
            + '</div>'
          + '</div>'
        + '</div>'
      );
    }
    function ensureCashboxesUi() {
      return;
    }

    function ensureEmployeesUi() {
      if (document.getElementById('employeesModal')) return;
      document.body.insertAdjacentHTML(
        'beforeend',
        ''
          + '<div class="modal" id="employeesModal">'
            + '<div class="dialog" style="width:min(1240px,100%);">'
              + '<div class="dialog__head">'
                + '<div class="dialog__title">СОТРУДНИКИ</div>'
                + '<button class="btn" data-close="employees">ЗАКРЫТЬ</button>'
              + '</div>'
              + '<div class="employees-layout">'
                + '<div class="employees-pane employees-pane--list">'
                  + '<div class="subpanel">'
                    + '<div class="employees-panel-head"><div class="panel-title">СПИСОК СОТРУДНИКОВ</div><div class="employees-list-meta" id="employeesListMeta">СПИСОК ПУСТ</div></div>'
                    + '<div class="employees-list-tools">'
                      + '<input class="repair-orders-search employees-search" id="employeesSearchInput" type="search" maxlength="80" placeholder="Поиск по имени или должности">'
                      + '<div class="employees-filterbar" id="employeesVisibilityFilters">'
                        + '<button class="btn is-active" type="button" data-filter="active">АКТИВНЫЕ</button>'
                        + '<button class="btn btn--ghost" type="button" data-filter="all">ВСЕ</button>'
                      + '</div>'
                    + '</div>'
                    + '<div class="employees-list" id="employeesList"></div>'
                  + '</div>'
                + '</div>'
                + '<div class="employees-pane">'
                  + '<div class="subpanel">'
                    + '<div class="employees-card-head">'
                      + '<div class="employees-card-head-main">'
                        + '<div class="panel-title">ПРОФИЛЬ</div>'
                        + '<div class="employees-card-title"><strong id="employeesCardMode">НОВЫЙ СОТРУДНИК</strong><div class="employees-profile-meta" id="employeesMeta">НОВЫЙ СОТРУДНИК</div></div>'
                      + '</div>'
                      + '<div class="employees-card-actions">'
                        + '<button class="btn btn--ghost" id="employeesCreateButton" type="button">НОВЫЙ</button>'
                        + '<button class="btn" id="employeeSaveButton" type="button">СОХРАНИТЬ</button>'
                        + '<button class="btn btn--ghost" id="employeeDeleteButton" type="button">УДАЛИТЬ</button>'
                      + '</div>'
                    + '</div>'
                    + '<div class="employees-form-grid">'
                      + '<div class="field employees-field--span-6"><label for="employeeNameInput">ИМЯ</label><input id="employeeNameInput" type="text" maxlength="80"></div>'
                      + '<div class="field employees-field--span-6"><label for="employeePositionInput">ДОЛЖНОСТЬ</label><input id="employeePositionInput" type="text" maxlength="80"></div>'
                      + '<div class="field employees-field--span-6 employees-field--compact employees-field--mode"><label for="employeeSalaryModeInput">СХЕМА</label><select id="employeeSalaryModeInput"><option value="salary_plus_percent">ОКЛАД + %</option><option value="percent_only">% ОТ РАБОТ</option><option value="salary_only">ТОЛЬКО ОКЛАД</option></select></div>'
                      + '<div class="field employees-field--span-2 employees-field--compact employees-field--salary"><label for="employeeBaseSalaryInput">ОКЛАД</label><input id="employeeBaseSalaryInput" type="text" inputmode="decimal" maxlength="40" placeholder="0"></div>'
                      + '<div class="field employees-field--span-2 employees-field--compact employees-field--percent"><label for="employeeWorkPercentInput">ПРОЦЕНТ</label><input id="employeeWorkPercentInput" type="text" inputmode="decimal" maxlength="40" placeholder="0"></div>'
                      + '<label class="employees-check employees-field--span-2 employees-field--active"><input id="employeeActiveInput" type="checkbox" checked> АКТИВЕН</label>'
                    + '</div>'
                    + '<details class="employees-note-details" id="employeeNoteDetails"><summary>ЗАМЕТКА</summary><div class="field field--secondary"><input id="employeeNoteInput" type="text" maxlength="240"></div></details>'
                    + '<div class="employees-summary-strip" id="employeesSummaryStrip"></div>'
                  + '</div>'
                  + '<div class="subpanel">'
                    + '<div class="employees-panel-head"><div class="panel-title">ОТЧЁТ ПО СОТРУДНИКУ</div><div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;"><div class="employees-report-meta" id="employeesReportMeta">0 СТРОК</div><input class="repair-orders-search" id="employeesMonthInput" type="month"></div></div>'
                    + '<div class="employees-report-shell">'
                      + '<div class="employees-report-tabs" id="employeesReportTabs">'
                        + '<button class="btn is-active" type="button" data-employees-report-tab="summary">СВОДКА</button>'
                        + '<button class="btn btn--ghost" type="button" data-employees-report-tab="details">СТРОКИ</button>'
                      + '</div>'
                      + '<div class="employees-report-panel is-active" id="employeesSummaryPanel"><div class="employees-table-wrap"><table class="employees-table"><thead><tr><th>СОТРУДНИК</th><th>ДОЛЖНОСТЬ</th><th class="is-num">КОЛ-ВО</th><th class="is-num">НАЧИСЛЕНО %</th><th class="is-num">ОКЛАД</th><th class="is-num">ИТОГ</th></tr></thead><tbody id="employeesSummaryTable"></tbody></table></div></div>'
                      + '<div class="employees-report-panel" id="employeesDetailsPanel"><div class="employees-table-wrap"><table class="employees-table"><thead><tr><th>ДАТА</th><th>НАРЯД</th><th>АВТО</th><th>РАБОТА</th><th class="is-num">СУММА</th><th class="is-num">НАЧИСЛЕНО</th></tr></thead><tbody id="employeesDetailTable"></tbody></table></div></div>'
                    + '</div>'
                  + '</div>'
                + '</div>'
              + '</div>'
            + '</div>'
          + '</div>'
      );
    }

    ensureRepairOrderPaymentsUi();
    ensureCashboxesUi();

    const els = {
      boardScroll: document.querySelector('.board-scroll'),
      board: document.getElementById('board'),
      statusLine: document.getElementById('statusLine'),
      boardSettingsButton: document.getElementById('boardSettingsButton'),
      topbarStatusHost: document.getElementById('topbarStatusHost'),
      stickyDockButton: document.getElementById('stickyDockButton'),
      agentDockButton: document.getElementById('agentDockButton'),
      aiChatButton: document.getElementById('aiChatButton'),
      operatorButton: document.getElementById('operatorButton'),
      archiveButton: document.getElementById('archiveButton'),
      repairOrdersButton: document.getElementById('repairOrdersButton'),
      cashboxesButton: document.getElementById('cashboxesButton'),
      employeesButton: document.getElementById('employeesButton'),
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
      boardControlSettingsRow: document.getElementById('boardControlSettingsRow'),
      boardControlToggle: document.getElementById('boardControlToggle'),
      boardControlIntervalInput: document.getElementById('boardControlIntervalInput'),
      boardControlCooldownInput: document.getElementById('boardControlCooldownInput'),
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
      cashboxesModal: document.getElementById('cashboxesModal'),
      cashboxTransferModal: document.getElementById('cashboxTransferModal'),
      employeesModal: document.getElementById('employeesModal'),
      employeesList: document.getElementById('employeesList'),
      employeesCardMode: document.getElementById('employeesCardMode'),
      employeesSearchInput: document.getElementById('employeesSearchInput'),
      employeesVisibilityFilters: document.getElementById('employeesVisibilityFilters'),
      employeesListMeta: document.getElementById('employeesListMeta'),
      employeesReportTabs: document.getElementById('employeesReportTabs'),
      employeesSummaryPanel: document.getElementById('employeesSummaryPanel'),
      employeesDetailsPanel: document.getElementById('employeesDetailsPanel'),
      employeesMonthInput: document.getElementById('employeesMonthInput'),
      employeesMeta: document.getElementById('employeesMeta'),
      employeesReportMeta: document.getElementById('employeesReportMeta'),
      employeesSummaryStrip: document.getElementById('employeesSummaryStrip'),
      employeesSummaryTable: document.getElementById('employeesSummaryTable'),
      employeesDetailTable: document.getElementById('employeesDetailTable'),
      employeesCreateButton: document.getElementById('employeesCreateButton'),
      employeeNameInput: document.getElementById('employeeNameInput'),
      employeePositionInput: document.getElementById('employeePositionInput'),
      employeeSalaryModeInput: document.getElementById('employeeSalaryModeInput'),
      employeeBaseSalaryInput: document.getElementById('employeeBaseSalaryInput'),
      employeeWorkPercentInput: document.getElementById('employeeWorkPercentInput'),
      employeeNoteDetails: document.getElementById('employeeNoteDetails'),
      employeeNoteInput: document.getElementById('employeeNoteInput'),
      employeeActiveInput: document.getElementById('employeeActiveInput'),
      employeeSaveButton: document.getElementById('employeeSaveButton'),
      employeeDeleteButton: document.getElementById('employeeDeleteButton'),
      cashboxesList: document.getElementById('cashboxesList'),
      cashboxCreateButton: document.getElementById('cashboxCreateButton'),
      cashboxDeleteButton: document.getElementById('cashboxDeleteButton'),
      cashboxDetailTitle: document.getElementById('cashboxDetailTitle'),
      cashboxDetailMeta: document.getElementById('cashboxDetailMeta'),
      cashboxStats: document.getElementById('cashboxStats'),
      cashboxAmountInput: document.getElementById('cashboxAmountInput'),
      cashboxNoteInput: document.getElementById('cashboxNoteInput'),
      cashboxIncomeButton: document.getElementById('cashboxIncomeButton'),
      cashboxTransferButton: document.getElementById('cashboxTransferButton'),
      cashboxExpenseButton: document.getElementById('cashboxExpenseButton'),
      cashboxTransactions: document.getElementById('cashboxTransactions'),
      cashboxTransferSourceName: document.getElementById('cashboxTransferSourceName'),
      cashboxTransferTargets: document.getElementById('cashboxTransferTargets'),
      cashboxTransferAmountInput: document.getElementById('cashboxTransferAmountInput'),
      cashboxTransferNoteInput: document.getElementById('cashboxTransferNoteInput'),
      cashboxTransferConfirmButton: document.getElementById('cashboxTransferConfirmButton'),
      repairOrdersOpenTab: document.getElementById('repairOrdersOpenTab'),
      repairOrdersClosedTab: document.getElementById('repairOrdersClosedTab'),
      repairOrdersMeta: document.getElementById('repairOrdersMeta'),
      repairOrdersTableHead: document.getElementById('repairOrdersTableHead'),
      repairOrdersList: document.getElementById('repairOrdersList'),
      gptWallModal: document.getElementById('gptWallModal'),
      gptWallMeta: document.getElementById('gptWallMeta'),
      gptWallText: document.getElementById('gptWallText'),
      gptWallBoardTab: document.getElementById('gptWallBoardTab'),
      gptWallEventsTab: document.getElementById('gptWallEventsTab'),
      gptWallRefresh: document.getElementById('gptWallRefresh'),
      aiSurfaceModal: document.getElementById('aiSurfaceModal'),
      aiSurfaceContextLabel: document.getElementById('aiSurfaceContextLabel'),
      aiSurfaceStatusLabel: document.getElementById('aiSurfaceStatusLabel'),
      aiSurfaceSummary: document.getElementById('aiSurfaceSummary'),
      aiSurfaceScenarioGrid: document.getElementById('aiSurfaceScenarioGrid'),
      aiSurfaceResult: document.getElementById('aiSurfaceResult'),
      aiSurfaceLegacyButton: document.getElementById('aiSurfaceLegacyButton'),
      aiChatWindow: document.getElementById('aiChatWindow'),
      aiChatWindowTitle: document.getElementById('aiChatWindowTitle'),
      aiChatWindowContextLabel: document.getElementById('aiChatWindowContextLabel'),
      aiChatWindowSubtitle: document.getElementById('aiChatWindowSubtitle'),
      aiChatWindowStatusLabel: document.getElementById('aiChatWindowStatusLabel'),
      aiChatWindowMessages: document.getElementById('aiChatWindowMessages'),
      aiChatWindowInput: document.getElementById('aiChatWindowInput'),
      aiChatWindowSettingsPane: document.getElementById('aiChatWindowSettingsPane'),
      aiChatWindowPromptSystem: document.getElementById('aiChatWindowPromptSystem'),
      aiChatWindowPromptResponse: document.getElementById('aiChatWindowPromptResponse'),
      aiChatWindowPromptProfileInput: document.getElementById('aiChatWindowPromptProfileInput'),
      aiChatWindowSettingsButton: document.getElementById('aiChatWindowSettingsButton'),
      aiChatWindowSendButton: document.getElementById('aiChatWindowSendButton'),
      agentModal: document.getElementById('agentModal'),
      agentContextLabel: document.getElementById('agentContextLabel'),
      agentStatusLabel: document.getElementById('agentStatusLabel'),
      agentQuickActions: document.getElementById('agentQuickActions'),
      agentTaskInput: document.getElementById('agentTaskInput'),
      agentAutofillButton: document.getElementById('agentAutofillButton'),
      agentAutofillPromptToggle: document.getElementById('agentAutofillPromptToggle'),
      agentAutofillPromptPanel: document.getElementById('agentAutofillPromptPanel'),
      agentAutofillPromptInput: document.getElementById('agentAutofillPromptInput'),
      agentAutofillPromptSaveButton: document.getElementById('agentAutofillPromptSaveButton'),
      agentAutofillPromptResetButton: document.getElementById('agentAutofillPromptResetButton'),
      agentAutofillStatus: document.getElementById('agentAutofillStatus'),
      agentRunButton: document.getElementById('agentRunButton'),
      agentResultPanel: document.getElementById('agentResultPanel'),
      agentRunsDetails: document.getElementById('agentRunsDetails'),
      agentRunsList: document.getElementById('agentRunsList'),
      agentActionsList: document.getElementById('agentActionsList'),
      agentDetails: document.getElementById('agentDetails'),
      agentTasksModal: document.getElementById('agentTasksModal'),
      agentTasksMeta: document.getElementById('agentTasksMeta'),
      agentTasksList: document.getElementById('agentTasksList'),
      agentTasksNewButton: document.getElementById('agentTasksNewButton'),
      agentTasksEditorTitle: document.getElementById('agentTasksEditorTitle'),
      agentTaskNameInput: document.getElementById('agentTaskNameInput'),
      agentTaskPromptInput: document.getElementById('agentTaskPromptInput'),
      agentTaskScopeTypeInput: document.getElementById('agentTaskScopeTypeInput'),
      agentTaskScopeColumnInput: document.getElementById('agentTaskScopeColumnInput'),
      agentTaskScheduleTypeInput: document.getElementById('agentTaskScheduleTypeInput'),
      agentTaskIntervalValueInput: document.getElementById('agentTaskIntervalValueInput'),
      agentTaskIntervalUnitInput: document.getElementById('agentTaskIntervalUnitInput'),
      agentTaskActiveInput: document.getElementById('agentTaskActiveInput'),
      agentTaskFormMeta: document.getElementById('agentTaskFormMeta'),
      agentTaskSaveButton: document.getElementById('agentTaskSaveButton'),
      agentTaskRunButton: document.getElementById('agentTaskRunButton'),
      agentTaskResetButton: document.getElementById('agentTaskResetButton'),
      cardModal: document.getElementById('cardModal'),
      cardModalTitle: document.getElementById('cardModalTitle'),
      cardModalCloseButtonTop: document.getElementById('cardModalCloseButtonTop'),
      cardModalCloseButtonBottom: document.getElementById('cardModalCloseButtonBottom'),
      cardMetaLine: document.getElementById('cardMetaLine'),
      cardVehicle: document.getElementById('cardVehicle'),
      cardTitle: document.getElementById('cardTitle'),
      cardDescription: document.getElementById('cardDescription'),
      signalPreview: document.getElementById('signalPreview'),
      signalDays: document.getElementById('signalDays'),
      signalHours: document.getElementById('signalHours'),
      signalDaysDisplay: document.getElementById('signalDaysDisplay'),
      signalHoursDisplay: document.getElementById('signalHoursDisplay'),
      signalDaysIncrementButton: document.getElementById('signalDaysIncrementButton'),
      signalDaysDecrementButton: document.getElementById('signalDaysDecrementButton'),
      signalHoursIncrementButton: document.getElementById('signalHoursIncrementButton'),
      signalHoursDecrementButton: document.getElementById('signalHoursDecrementButton'),
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
      repairOrderPaymentMethod: document.getElementById('repairOrderPaymentMethod'),
      repairOrderPrepayment: document.getElementById('repairOrderPrepayment'),
      repairOrderPaymentsButton: document.getElementById('repairOrderPaymentsButton'),
      repairOrderPaymentsModal: document.getElementById('repairOrderPaymentsModal'),
      repairOrderPaymentsMeta: document.getElementById('repairOrderPaymentsMeta'),
      repairOrderPaymentsList: document.getElementById('repairOrderPaymentsList'),
      repairOrderPaymentCashbox: document.getElementById('repairOrderPaymentCashbox'),
      repairOrderPaymentAmount: document.getElementById('repairOrderPaymentAmount'),
      repairOrderPaymentNote: document.getElementById('repairOrderPaymentNote'),
      repairOrderPaymentAddButton: document.getElementById('repairOrderPaymentAddButton'),
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
      cardAgentButton: document.getElementById('cardAgentButton'),
      fileDropzone: document.getElementById('fileDropzone'),
      fileDropMeta: document.getElementById('fileDropMeta'),
      fileInput: document.getElementById('fileInput'),
      uploadButton: document.getElementById('uploadButton'),
      fileList: document.getElementById('fileList'),
      logList: document.getElementById('logList'),
    };

    const escapeHtml = (value) => String(value ?? '').replace(/[&<>"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[char]));

    function hydrateEmployeesUiRefs() {
      els.employeesModal = document.getElementById('employeesModal');
      els.employeesList = document.getElementById('employeesList');
      els.employeesCardMode = document.getElementById('employeesCardMode');
      els.employeesSearchInput = document.getElementById('employeesSearchInput');
      els.employeesVisibilityFilters = document.getElementById('employeesVisibilityFilters');
      els.employeesListMeta = document.getElementById('employeesListMeta');
      els.employeesReportTabs = document.getElementById('employeesReportTabs');
      els.employeesSummaryPanel = document.getElementById('employeesSummaryPanel');
      els.employeesDetailsPanel = document.getElementById('employeesDetailsPanel');
      els.employeesMonthInput = document.getElementById('employeesMonthInput');
      els.employeesMeta = document.getElementById('employeesMeta');
      els.employeesReportMeta = document.getElementById('employeesReportMeta');
      els.employeesSummaryStrip = document.getElementById('employeesSummaryStrip');
      els.employeesSummaryTable = document.getElementById('employeesSummaryTable');
      els.employeesDetailTable = document.getElementById('employeesDetailTable');
      els.employeesCreateButton = document.getElementById('employeesCreateButton');
      els.employeeNameInput = document.getElementById('employeeNameInput');
      els.employeePositionInput = document.getElementById('employeePositionInput');
      els.employeeSalaryModeInput = document.getElementById('employeeSalaryModeInput');
      els.employeeBaseSalaryInput = document.getElementById('employeeBaseSalaryInput');
      els.employeeWorkPercentInput = document.getElementById('employeeWorkPercentInput');
      els.employeeNoteDetails = document.getElementById('employeeNoteDetails');
      els.employeeNoteInput = document.getElementById('employeeNoteInput');
      els.employeeActiveInput = document.getElementById('employeeActiveInput');
      els.employeeSaveButton = document.getElementById('employeeSaveButton');
      els.employeeDeleteButton = document.getElementById('employeeDeleteButton');
    }

    function hydrateRepairOrderPaymentsUiRefs() {
      els.repairOrderPaymentsModal = document.getElementById('repairOrderPaymentsModal');
      els.repairOrderPaymentsList = document.getElementById('repairOrderPaymentsList');
      els.repairOrderPaymentCashbox = document.getElementById('repairOrderPaymentCashbox');
      els.repairOrderPaymentAmount = document.getElementById('repairOrderPaymentAmount');
      els.repairOrderPaymentNote = document.getElementById('repairOrderPaymentNote');
      els.repairOrderPaymentAddButton = document.getElementById('repairOrderPaymentAddButton');
    }

    function bindRepairOrderPaymentsUiEvents() {
      if (state.repairOrderPaymentsUiBound) return;
      hydrateRepairOrderPaymentsUiRefs();
      els.repairOrderPaymentAddButton?.addEventListener('click', addRepairOrderPayment);
      els.repairOrderPaymentsList?.addEventListener('click', handleRepairOrderPaymentsListClick);
      els.repairOrderPaymentCashbox?.addEventListener('change', handleRepairOrderPaymentsFormChange);
      els.repairOrderPaymentAmount?.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          addRepairOrderPayment();
        }
      });
      els.repairOrderPaymentNote?.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          addRepairOrderPayment();
        }
      });
      els.repairOrderPaymentsModal?.addEventListener('click', handleRepairOrderPaymentsModalOverlayClick);
      state.repairOrderPaymentsUiBound = true;
    }

    function hydrateAgentUiRefs() {
      els.agentModal = document.getElementById('agentModal');
      els.agentContextLabel = document.getElementById('agentContextLabel');
      els.agentStatusLabel = document.getElementById('agentStatusLabel');
      els.agentQuickActions = document.getElementById('agentQuickActions');
      els.agentTasksOpenButton = document.getElementById('agentTasksOpenButton');
      els.agentTaskInput = document.getElementById('agentTaskInput');
      els.agentAutofillButton = document.getElementById('agentAutofillButton');
      els.agentAutofillPromptToggle = document.getElementById('agentAutofillPromptToggle');
      els.agentAutofillPromptPanel = document.getElementById('agentAutofillPromptPanel');
      els.agentAutofillPromptInput = document.getElementById('agentAutofillPromptInput');
      els.agentAutofillPromptSaveButton = document.getElementById('agentAutofillPromptSaveButton');
      els.agentAutofillPromptResetButton = document.getElementById('agentAutofillPromptResetButton');
      els.agentAutofillStatus = document.getElementById('agentAutofillStatus');
      els.agentRunButton = document.getElementById('agentRunButton');
      els.agentResultPanel = document.getElementById('agentResultPanel');
      els.agentRunsDetails = document.getElementById('agentRunsDetails');
      els.agentRunsList = document.getElementById('agentRunsList');
      els.agentActionsList = document.getElementById('agentActionsList');
      els.agentDetails = document.getElementById('agentDetails');
    }

    function hydrateAiSurfaceUiRefs() {
      els.aiSurfaceModal = document.getElementById('aiSurfaceModal');
      els.aiSurfaceContextLabel = document.getElementById('aiSurfaceContextLabel');
      els.aiSurfaceStatusLabel = document.getElementById('aiSurfaceStatusLabel');
      els.aiSurfaceSummary = document.getElementById('aiSurfaceSummary');
      els.aiSurfaceScenarioGrid = document.getElementById('aiSurfaceScenarioGrid');
      els.aiSurfaceResult = document.getElementById('aiSurfaceResult');
      els.aiSurfaceLegacyButton = document.getElementById('aiSurfaceLegacyButton');
      els.aiChatWindow = document.getElementById('aiChatWindow');
      els.aiChatWindowTitle = document.getElementById('aiChatWindowTitle');
      els.aiChatWindowContextLabel = document.getElementById('aiChatWindowContextLabel');
      els.aiChatWindowSubtitle = document.getElementById('aiChatWindowSubtitle');
      els.aiChatWindowStatusLabel = document.getElementById('aiChatWindowStatusLabel');
      els.aiChatWindowMessages = document.getElementById('aiChatWindowMessages');
      els.aiChatWindowInput = document.getElementById('aiChatWindowInput');
      els.aiChatWindowSettingsPane = document.getElementById('aiChatWindowSettingsPane');
      els.aiChatWindowPromptSystem = document.getElementById('aiChatWindowPromptSystem');
      els.aiChatWindowPromptResponse = document.getElementById('aiChatWindowPromptResponse');
      els.aiChatWindowPromptProfileInput = document.getElementById('aiChatWindowPromptProfileInput');
      els.aiChatWindowSettingsButton = document.getElementById('aiChatWindowSettingsButton');
      els.aiChatWindowSendButton = document.getElementById('aiChatWindowSendButton');
      els.aiChatButton = document.getElementById('aiChatButton');
    }

    function hydrateAiChatWindowUiRefs() {
      els.aiChatWindow = document.getElementById('aiChatWindow');
      els.aiChatWindowTitle = document.getElementById('aiChatWindowTitle');
      els.aiChatWindowContextLabel = document.getElementById('aiChatWindowContextLabel');
      els.aiChatWindowSubtitle = document.getElementById('aiChatWindowSubtitle');
      els.aiChatWindowStatusLabel = document.getElementById('aiChatWindowStatusLabel');
      els.aiChatWindowMessages = document.getElementById('aiChatWindowMessages');
      els.aiChatWindowInput = document.getElementById('aiChatWindowInput');
      els.aiChatWindowSettingsButton = document.getElementById('aiChatWindowSettingsButton');
      els.aiChatWindowSendButton = document.getElementById('aiChatWindowSendButton');
    }

    function normalizeAiChatMessageRole(role) {
      const normalized = String(role || '').trim().toLowerCase();
      if (normalized === 'assistant' || normalized === 'system' || normalized === 'status') return normalized;
      return 'user';
    }

    function aiChatHistoryContextSnapshot() {
      const source = state.aiCompactContext && typeof state.aiCompactContext === 'object'
        ? state.aiCompactContext
        : (state.aiChatWindowContext && typeof state.aiChatWindowContext === 'object'
          ? state.aiChatWindowContext
          : buildAiCompactContextPacket());
      const profile = ensureAiChatWindowPromptProfile();
      const knowledge = state.aiChatWindowKnowledge && typeof state.aiChatWindowKnowledge === 'object'
        ? state.aiChatWindowKnowledge
        : null;
      return {
        kind: String(source.kind || 'chat').trim().toLowerCase() || 'chat',
        card_id: String(source.card_id || '').trim(),
        repair_order_id: String(source.repair_order_id || '').trim(),
        source_kind: String(source.kind || 'chat').trim().toLowerCase() || 'chat',
        card_label: String(source.card_label || '').trim(),
        repair_order_label: String(source.repair_order_label || '').trim(),
        repair_order_context_label: String(source.repair_order_context_label || '').trim(),
        context_label: String(source.context_label || '').trim(),
        wall_digest_label: String(source.wall_digest?.summary_label || '').trim(),
        wall_view: String(source.wall_context?.view || '').trim(),
        wall_available: Boolean(source.wall_context?.has_wall),
        attachments_ready: Boolean(source.attachments_intake?.ready),
        attachments_label: String(source.attachments_intake?.label || '').trim(),
        attachments_card_count: String(source.attachments_intake?.card_attachment_count ?? '').trim(),
        attachments_repair_order_count: String(source.attachments_intake?.repair_order_attachment_count ?? '').trim(),
        attachments_total_count: String(source.attachments_intake?.total_attachment_count ?? '').trim(),
        attachments_card_ids: Array.isArray(source.attachments_intake?.card_attachment_ids) ? source.attachments_intake.card_attachment_ids.slice(0, 12) : [],
        attachments_repair_order_ids: Array.isArray(source.attachments_intake?.repair_order_attachment_ids) ? source.attachments_intake.repair_order_attachment_ids.slice(0, 12) : [],
        knowledge_source_labels: Array.isArray(knowledge?.source_labels) ? knowledge.source_labels.slice(0, 6) : [],
        compact_context_kind: String(source.kind || 'chat').trim().toLowerCase() || 'chat',
        prompt_profile_kind: 'ai_chat',
        prompt_profile_user_tune: String(profile.user_tune || '').trim(),
      };
    }

    function createAiChatMessage(role, text, meta = {}) {
      const normalizedRole = normalizeAiChatMessageRole(role);
      const entry = {
        id: 'ai-chat-' + (++state.aiChatWindowMessageSeq),
        role: normalizedRole,
        text: String(text || '').trim(),
        tone: normalizedRole === 'system' || normalizedRole === 'status' ? 'idle' : normalizedRole,
        created_at: new Date().toISOString(),
        context: aiChatHistoryContextSnapshot(),
      };
      const source = meta && typeof meta === 'object' ? meta : {};
      if (source.source) entry.source = String(source.source || '').trim();
      if (source.state) entry.state = String(source.state || '').trim().toLowerCase();
      if (source.kind) entry.kind = String(source.kind || '').trim().toLowerCase();
      if (source.topic) entry.topic = String(source.topic || '').trim();
      if (source.hint) entry.hint = String(source.hint || '').trim();
      if (Array.isArray(source.knowledge_source_labels)) {
        entry.knowledge_source_labels = source.knowledge_source_labels.map((item) => String(item || '').trim()).filter(Boolean).slice(0, 6);
      }
      return entry;
    }

    function ensureAiChatWindowHistory() {
      if (!Array.isArray(state.aiChatWindowHistory)) state.aiChatWindowHistory = [];
      if (state.aiChatWindowHistory.length > 0) return state.aiChatWindowHistory;
      state.aiChatWindowHistory.push(
        createAiChatMessage('system', 'Чат-окно готово как отдельный surface. Здесь позже появятся полноценный runtime, markdown и context wiring.', { kind: 'shell', source: 'shell' }),
        createAiChatMessage('assistant', 'Это рабочий shell нового AI-чата. Пользовательские сообщения уже сохраняются в локальную историю.', { kind: 'shell', source: 'shell' })
      );
      return state.aiChatWindowHistory;
    }

    function ensureAiChatWindowPromptProfile() {
      if (!state.aiChatWindowPromptProfile || typeof state.aiChatWindowPromptProfile !== 'object') {
        state.aiChatWindowPromptProfile = {
          system_instruction: 'Чат отвечает как отдельный рабочий AI surface AutoStop CRM.',
          response_profile: 'Кратко, структурно, с читаемыми блоками и без лишнего шума.',
          user_tune: '',
        };
      }
      if (typeof state.aiChatWindowPromptProfile.system_instruction !== 'string') {
        state.aiChatWindowPromptProfile.system_instruction = 'Чат отвечает как отдельный рабочий AI surface AutoStop CRM.';
      }
      if (typeof state.aiChatWindowPromptProfile.response_profile !== 'string') {
        state.aiChatWindowPromptProfile.response_profile = 'Кратко, структурно, с читаемыми блоками и без лишнего шума.';
      }
      if (typeof state.aiChatWindowPromptProfile.user_tune !== 'string') {
        state.aiChatWindowPromptProfile.user_tune = '';
      }
      return state.aiChatWindowPromptProfile;
    }

    function aiChatContextCardLabel(card = state.activeCard) {
      const payload = currentCardPayload();
      const cardId = String(card?.id || state.editingId || payload?.id || '').trim();
      const cardTitle = String(payload?.title || card?.title || '').trim();
      const cardVehicle = String(payload?.vehicle || card?.vehicle || '').trim();
      if (!cardId && !cardTitle && !cardVehicle) return 'AI-ЧАТ · СВОБОДНАЯ СЕССИЯ';
      const parts = ['AI-ЧАТ', 'КАРТОЧКА' + (cardId ? ' #' + cardId : '')];
      if (cardVehicle) parts.push(cardVehicle);
      if (cardTitle) parts.push(cardTitle);
      return parts.join(' · ');
    }

    function aiChatContextRepairOrderLabel(repairOrder = null) {
      const order = repairOrder && typeof repairOrder === 'object' ? repairOrder : {};
      const repairOrderNumber = String(order?.number || order?.id || '').trim();
      const statusLabel = String(order?.status_label || order?.status || '').trim();
      const source = repairOrder && typeof repairOrder === 'object' ? repairOrder : {};
      if (!repairOrderNumber && !statusLabel) return '';
      const parts = ['ЗАКАЗ-НАРЯД' + (repairOrderNumber ? ' #' + repairOrderNumber : '')];
      if (statusLabel) parts.push(statusLabel);
      return parts.join(' · ');
    }

    function aiChatActiveRepairOrderScope() {
      const currentCard = state.activeCard && typeof state.activeCard === 'object' ? state.activeCard : null;
      if (!currentCard) return null;
      const cardDraft = repairOrderCardDraft(currentCard, currentCard?.repair_order || {});
      if (els.repairOrderModal?.classList.contains('is-open')) {
        return readRepairOrderFromForm();
      }
      return cardDraft;
    }

    function aiWallDigestShortText(source, fallback = '') {
      const value = String(source || fallback || '').replace(/\\r?\\n/g, ' ').replace(/\\s+/g, ' ').trim();
      if (!value) return '';
      if (value.length <= 160) return value;
      return value.slice(0, 157).replace(/[,.;:-]+$/, '') + '…';
    }

    const AI_COMPACT_CONTEXT_LIMITS = Object.freeze({
      wall_key_facts: 4,
      wall_notable_changes: 3,
      wall_important_notes: 5,
      wall_vehicle_signals: 4,
      wall_client_signals: 4,
      wall_work_signals: 4,
      wall_symptom_signals: 4,
      wall_agreement_signals: 4,
      card_key_fields: 6,
      card_ai_facts: 6,
      repair_key_fields: 6,
      repair_ai_facts: 8,
      repair_work_summary: 4,
      repair_material_summary: 4,
      attachment_items: 8,
      attachment_label: 96,
    });

    function aiCompactTrimList(list, limit = 0) {
      if (!Array.isArray(list)) return [];
      const normalizedLimit = Number(limit || 0);
      if (!Number.isFinite(normalizedLimit) || normalizedLimit <= 0) return list.slice();
      return list.slice(0, normalizedLimit);
    }

    function aiCompactTrimText(source, fallback = '', limit = 160) {
      const value = aiWallDigestShortText(source, fallback);
      const normalizedLimit = Number(limit || 0);
      if (!Number.isFinite(normalizedLimit) || normalizedLimit <= 0 || value.length <= normalizedLimit) return value;
      return value.slice(0, Math.max(0, normalizedLimit - 1)).replace(/[,.;:-]+$/, '') + '…';
    }

    function aiCompactPickFields(source, fields = []) {
      const payload = source && typeof source === 'object' ? source : {};
      return fields.reduce((result, fieldName) => {
        if (fieldName in payload && payload[fieldName] !== undefined && payload[fieldName] !== null) {
          result[fieldName] = payload[fieldName];
        }
        return result;
      }, {});
    }

    function aiCompactContextPacketSignature(packet) {
      const source = packet && typeof packet === 'object' ? packet : {};
      const card = source.card_context && typeof source.card_context === 'object' ? source.card_context : {};
      const repairOrder = source.repair_order_context && typeof source.repair_order_context === 'object' ? source.repair_order_context : {};
      const wallDigest = source.wall_digest && typeof source.wall_digest === 'object' ? source.wall_digest : {};
      const attachments = source.attachments_intake && typeof source.attachments_intake === 'object' ? source.attachments_intake : {};
      return [
        String(source.kind || '').trim(),
        String(source.surface || '').trim(),
        String(source.card_id || '').trim(),
        String(source.repair_order_id || '').trim(),
        String(card.summary_label || '').trim(),
        String(card.card_id || '').trim(),
        String(repairOrder.summary_label || '').trim(),
        String(repairOrder.repair_order_id || '').trim(),
        String(wallDigest.generated_at || '').trim(),
        String(wallDigest.summary_label || wallDigest.label || '').trim(),
        String(wallDigest.view || '').trim(),
        String(attachments.label || '').trim(),
        String(attachments.total_attachment_count || '').trim(),
        String(attachments.card_attachment_count || '').trim(),
        String(attachments.repair_order_attachment_count || '').trim(),
        String(attachments.items?.[0]?.attachment_id || '').trim(),
        String(state.aiChatWindowPromptProfile?.user_tune || '').trim(),
      ].join('::');
    }

    function buildAiScenarioContextPacket(scenarioId = 'ai_chat') {
      const packet = buildAiCompactContextPacket();
      const normalizedScenarioId = String(scenarioId || 'ai_chat').trim().toLowerCase() || 'ai_chat';
      return {
        ...packet,
        kind: 'compact_context',
        scenario_id: normalizedScenarioId,
        scenario_kind: normalizedScenarioId,
        scenario_context_kind: normalizedScenarioId,
      };
    }

    function buildAiFullCardEnrichmentContextPacket() {
      return buildAiScenarioContextPacket('full_card_enrichment');
    }

    function buildAiChatWindowContext() {
      const packet = buildAiCompactContextPacket();
      return {
        ...packet,
        kind: 'chat',
        scenario_id: 'ai_chat',
        scenario_kind: 'ai_chat',
        scenario_context_kind: 'ai_chat',
      };
    }

    function aiWallDigestEventSummary(event) {
      if (!event || typeof event !== 'object') return '';
      const parts = [];
      const timestamp = String(event.timestamp || '').trim();
      const actor = String(event.actor_name || '').trim();
      const card = String(event.card_short_id || event.card_id || '').trim();
      const message = aiWallDigestShortText(event.message || event.details_text || event.card_heading || '');
      if (timestamp) parts.push(timestamp);
      if (actor) parts.push(actor);
      if (card) parts.push(card);
      if (message) parts.push(message);
      return parts.join(' · ');
    }

    function aiWallDigestCardSignal(card) {
      const payload = card && typeof card === 'object' ? card : {};
      const cardId = String(payload.short_id || payload.id || '').trim();
      const vehicle = String(payload.vehicle || '').trim();
      const title = String(payload.title || '').trim();
      const columnLabel = String(payload.column_label || payload.column || '').trim();
      const status = String(payload.status || '').trim();
      const repairOrder = payload.repair_order && typeof payload.repair_order === 'object' ? payload.repair_order : {};
      const signalParts = [];
      if (vehicle) signalParts.push(vehicle);
      if (title) signalParts.push(title);
      if (repairOrder.client) signalParts.push('клиент: ' + aiWallDigestShortText(repairOrder.client, repairOrder.client));
      if (repairOrder.reason) signalParts.push('симптом: ' + aiWallDigestShortText(repairOrder.reason, repairOrder.reason));
      if (repairOrder.comment || repairOrder.note) signalParts.push('договорённость: ' + aiWallDigestShortText(repairOrder.comment || repairOrder.note, repairOrder.comment || repairOrder.note));
      if (repairOrder.works?.length) signalParts.push('работы: ' + repairOrder.works.length);
      if (repairOrder.materials?.length) signalParts.push('материалы: ' + repairOrder.materials.length);
      if (status) signalParts.push('статус: ' + status);
      if (columnLabel) signalParts.push('колонка: ' + columnLabel);
      return {
        card_id: cardId,
        summary: signalParts.join(' · '),
      };
    }

    function buildAiWallDigestPacket(wall = state.gptWall) {
      const source = wall && typeof wall === 'object' ? wall : null;
      const meta = source?.meta && typeof source.meta === 'object' ? source.meta : {};
      const boardContext = source?.board_context && typeof source.board_context === 'object' ? source.board_context : {};
      const cards = Array.isArray(source?.cards) ? source.cards : [];
      const events = Array.isArray(source?.events) ? source.events : [];
      const stickies = Array.isArray(source?.stickies) ? source.stickies : [];
      const orderedCards = cards.slice().sort((left, right) => {
        const leftKey = String(right?.updated_at || right?.created_at || right?.id || '').trim();
        const rightKey = String(left?.updated_at || left?.created_at || left?.id || '').trim();
        return leftKey.localeCompare(rightKey);
      });
      const priorityCards = aiCompactTrimList(orderedCards, 5);
      const keyFacts = [
        meta.columns !== undefined ? 'Колонок: ' + meta.columns : '',
        meta.active_cards !== undefined ? 'Активных карточек: ' + meta.active_cards : '',
        meta.archived_cards !== undefined ? 'Архивных карточек: ' + meta.archived_cards : '',
        meta.stickies !== undefined ? 'Стикеров: ' + meta.stickies : '',
        meta.events_total !== undefined ? 'Событий: ' + meta.events_total : '',
      ].filter(Boolean);
      const notableChanges = aiCompactTrimList(events, AI_COMPACT_CONTEXT_LIMITS.wall_notable_changes).map((event) => aiWallDigestEventSummary(event)).filter(Boolean);
      const importantNotes = [];
      for (const sticky of aiCompactTrimList(stickies, AI_COMPACT_CONTEXT_LIMITS.wall_important_notes)) {
        const stickyText = aiCompactTrimText(sticky?.text || sticky?.content || sticky?.message || '');
        if (stickyText) importantNotes.push(stickyText);
      }
      for (const card of priorityCards) {
        const signal = aiWallDigestCardSignal(card);
        if (signal.summary) importantNotes.push(signal.card_id ? signal.card_id + ': ' + signal.summary : signal.summary);
      }
      const vehicleSignals = [];
      const clientSignals = [];
      const workSignals = [];
      const symptomSignals = [];
      const agreementSignals = [];
      for (const card of priorityCards) {
        const repairOrder = card?.repair_order && typeof card.repair_order === 'object' ? card.repair_order : {};
        const cardLabel = String(card?.short_id || card?.id || '').trim();
        const vehicle = String(card?.vehicle || repairOrder?.vehicle || '').trim();
        if (vehicle) vehicleSignals.push((cardLabel ? cardLabel + ': ' : '') + vehicle);
        if (repairOrder.client) clientSignals.push((cardLabel ? cardLabel + ': ' : '') + aiCompactTrimText(repairOrder.client, repairOrder.client));
        if (repairOrder.works?.length || repairOrder.materials?.length) {
          workSignals.push((cardLabel ? cardLabel + ': ' : '') + 'работы ' + (repairOrder.works?.length || 0) + ' / материалы ' + (repairOrder.materials?.length || 0));
        }
        if (repairOrder.reason || card?.description) symptomSignals.push((cardLabel ? cardLabel + ': ' : '') + aiCompactTrimText(repairOrder.reason || card?.description || '', repairOrder.reason || card?.description || ''));
        if (repairOrder.comment || repairOrder.note || repairOrder.prepayment_display) {
          agreementSignals.push((cardLabel ? cardLabel + ': ' : '') + aiCompactTrimText(repairOrder.comment || repairOrder.note || repairOrder.prepayment_display || '', repairOrder.comment || repairOrder.note || repairOrder.prepayment_display || ''));
        }
      }
      const summaryLabel = source
        ? 'СТЕНА · КОМПАКТНЫЙ ДАЙДЖЕСТ'
        : 'СТЕНА · ДАЙДЖЕСТ НЕДОСТУПЕН';
      return {
        kind: 'wall_digest',
        source_kind: source ? 'gpt_wall' : 'none',
        has_wall: Boolean(source),
        label: summaryLabel,
        summary_label: summaryLabel,
        generated_at: String(meta.generated_at || '').trim(),
        view: normalizeGptWallView(state.gptWallView),
        board_label: String(boardContext?.board_name || boardContext?.label || '').trim(),
        key_facts: aiCompactTrimList(keyFacts, AI_COMPACT_CONTEXT_LIMITS.wall_key_facts),
        notable_changes: aiCompactTrimList(notableChanges, AI_COMPACT_CONTEXT_LIMITS.wall_notable_changes),
        important_notes: aiCompactTrimList(importantNotes, AI_COMPACT_CONTEXT_LIMITS.wall_important_notes),
        vehicle_signals: aiCompactTrimList(vehicleSignals, AI_COMPACT_CONTEXT_LIMITS.wall_vehicle_signals),
        client_signals: aiCompactTrimList(clientSignals, AI_COMPACT_CONTEXT_LIMITS.wall_client_signals),
        work_signals: aiCompactTrimList(workSignals, AI_COMPACT_CONTEXT_LIMITS.wall_work_signals),
        symptom_signals: aiCompactTrimList(symptomSignals, AI_COMPACT_CONTEXT_LIMITS.wall_symptom_signals),
        agreement_signals: aiCompactTrimList(agreementSignals, AI_COMPACT_CONTEXT_LIMITS.wall_agreement_signals),
        summary_text: [
          summaryLabel,
          aiCompactTrimList(keyFacts, AI_COMPACT_CONTEXT_LIMITS.wall_key_facts).join(' · '),
          aiCompactTrimList(notableChanges, AI_COMPACT_CONTEXT_LIMITS.wall_notable_changes).join(' · '),
          aiCompactTrimList(importantNotes, AI_COMPACT_CONTEXT_LIMITS.wall_important_notes).join(' · '),
        ].filter(Boolean).join('\\n'),
      };
    }

    function buildAiCardContextPacket(card = state.activeCard, repairOrder = null, wallDigest = null) {
      const sourceCard = card && typeof card === 'object' ? card : null;
      const payload = currentCardPayload();
      const activeRepairOrder = repairOrder && typeof repairOrder === 'object' ? repairOrder : aiChatActiveRepairOrderScope();
      const digest = wallDigest && typeof wallDigest === 'object' ? wallDigest : (state.aiCompactContext?.wall_digest && typeof state.aiCompactContext.wall_digest === 'object' ? state.aiCompactContext.wall_digest : buildAiWallDigestPacket());
      const cardId = String(sourceCard?.id || state.editingId || payload?.id || '').trim();
      const cardShortId = String(sourceCard?.short_id || cardId || '').trim();
      const cardTitle = String(payload?.title || sourceCard?.title || '').trim();
      const cardVehicle = String(payload?.vehicle || sourceCard?.vehicle || '').trim();
      const cardColumn = String(payload?.column || sourceCard?.column || '').trim();
      const columnLabel = String(sourceCard?.column_label || '').trim();
      const status = String(sourceCard?.status || '').trim();
      const description = aiWallDigestShortText(payload?.description || sourceCard?.description || '', payload?.description || sourceCard?.description || '');
      const vehicleProfile = payload?.vehicle_profile && typeof payload.vehicle_profile === 'object'
        ? payload.vehicle_profile
        : (sourceCard?.vehicle_profile && typeof sourceCard.vehicle_profile === 'object' ? sourceCard.vehicle_profile : {});
      const vehicleProfileCompact = payload?.vehicle_profile_compact && typeof payload.vehicle_profile_compact === 'object'
        ? aiCompactPickFields(payload.vehicle_profile_compact, [
          'make_display',
          'model_display',
          'production_year',
          'mileage',
          'vin',
          'engine_model',
          'gearbox_model',
          'drivetrain',
          'display_name',
          'has_any_data',
          'source_summary',
          'source_confidence',
          'data_completion_state',
          'manual_fields',
          'autofilled_fields',
          'tentative_fields',
          'warnings',
        ])
        : null;
      const repairOrderLabel = aiChatContextRepairOrderLabel(activeRepairOrder);
      const keyFields = [
        cardVehicle ? { key: 'vehicle', label: 'Машина', value: cardVehicle } : null,
        cardTitle ? { key: 'title', label: 'Краткая суть', value: cardTitle } : null,
        status ? { key: 'status', label: 'Статус', value: status } : null,
        cardColumn || columnLabel ? { key: 'column', label: 'Колонка', value: columnLabel || cardColumn } : null,
        description ? { key: 'description', label: 'Описание', value: description } : null,
        sourceCard?.attachment_count !== undefined ? { key: 'attachments', label: 'Вложения', value: String(sourceCard.attachment_count) } : null,
        sourceCard?.events_count !== undefined ? { key: 'events', label: 'События', value: String(sourceCard.events_count) } : null,
      ].filter(Boolean);
      const aiRelevantFacts = {
        client: String(activeRepairOrder?.client || '').trim(),
        machine: String(activeRepairOrder?.vehicle || cardVehicle || vehicleProfile?.display_name || '').trim(),
        symptoms: String(activeRepairOrder?.reason || payload?.description || '').trim(),
        works: Array.isArray(activeRepairOrder?.works)
          ? activeRepairOrder.works.slice(0, 5).map((row) => aiWallDigestShortText(row?.name || row?.title || row?.work_name || row?.description || '', row?.name || row?.title || row?.work_name || row?.description || '')).filter(Boolean)
          : [],
        notes: [
          String(activeRepairOrder?.comment || '').trim(),
          String(activeRepairOrder?.note || '').trim(),
          String(activeRepairOrder?.prepayment_display || '').trim(),
        ].filter(Boolean),
      };
      return {
        kind: 'card_context',
        source_kind: sourceCard ? 'card' : 'workspace',
        card_id: cardId,
        card_short_id: cardShortId,
        title: cardTitle,
        summary_label: [cardShortId || cardId || 'CARD', cardVehicle, cardTitle].filter(Boolean).join(' · '),
        status,
        column: cardColumn,
        column_label: columnLabel,
        key_fields: aiCompactTrimList(keyFields, AI_COMPACT_CONTEXT_LIMITS.card_key_fields),
        ai_relevant_facts: aiRelevantFacts,
        repair_order_label: repairOrderLabel,
        wall_digest_label: String(digest?.label || '').trim(),
        wall_digest_summary: aiCompactTrimText(digest?.summary_text || '', digest?.summary_text || '', 360),
        wall_digest_key_facts: aiCompactTrimList(Array.isArray(digest?.key_facts) ? digest.key_facts : [], 4),
        wall_digest_notable_changes: aiCompactTrimList(Array.isArray(digest?.notable_changes) ? digest.notable_changes : [], 3),
        wall_digest_available: Boolean(digest?.has_wall),
        attachment_count: Number(sourceCard?.attachment_count ?? 0) || 0,
        events_count: Number(sourceCard?.events_count ?? 0) || 0,
        vehicle_profile_compact: vehicleProfileCompact || (vehicleProfile && typeof vehicleProfile === 'object'
          ? aiCompactPickFields(vehicleProfile, [
            'make_display',
            'model_display',
            'production_year',
            'mileage',
            'vin',
            'engine_model',
            'gearbox_model',
            'drivetrain',
            'display_name',
            'has_any_data',
            'source_summary',
            'source_confidence',
            'data_completion_state',
            'manual_fields',
            'autofilled_fields',
            'tentative_fields',
            'warnings',
          ])
          : null),
      };
    }

    function buildAiRepairOrderContextPacket(repairOrder = null, card = state.activeCard, wallDigest = null) {
      const sourceCard = card && typeof card === 'object' ? card : null;
      const activeRepairOrder = repairOrder && typeof repairOrder === 'object'
        ? repairOrder
        : (sourceCard?.repair_order && typeof sourceCard.repair_order === 'object'
          ? sourceCard.repair_order
          : aiChatActiveRepairOrderScope());
      const digest = wallDigest && typeof wallDigest === 'object' ? wallDigest : (state.aiCompactContext?.wall_digest && typeof state.aiCompactContext.wall_digest === 'object' ? state.aiCompactContext.wall_digest : buildAiWallDigestPacket());
      const repairOrderId = String(activeRepairOrder?.id || activeRepairOrder?.number || '').trim();
      const number = String(activeRepairOrder?.number || '').trim();
      const status = String(activeRepairOrder?.status || '').trim();
      const statusLabel = String(activeRepairOrder?.status_label || '').trim();
      const vehicleId = String(activeRepairOrder?.vehicle_id || sourceCard?.vehicle_id || '').trim();
      const client = String(activeRepairOrder?.client || '').trim();
      const phone = String(activeRepairOrder?.phone || '').trim();
      const vehicle = String(activeRepairOrder?.vehicle || sourceCard?.vehicle || '').trim();
      const licensePlate = String(activeRepairOrder?.license_plate || '').trim();
      const vin = String(activeRepairOrder?.vin || '').trim();
      const mileage = String(activeRepairOrder?.mileage || '').trim();
      const paymentMethod = String(activeRepairOrder?.payment_method || '').trim();
      const paymentMethodLabel = String(activeRepairOrder?.payment_method_label || '').trim();
      const paymentStatus = String(activeRepairOrder?.payment_status || '').trim();
      const paymentStatusLabel = String(activeRepairOrder?.payment_status_label || '').trim();
      const grandTotal = String(activeRepairOrder?.grand_total || '').trim();
      const dueTotal = String(activeRepairOrder?.due_total || '').trim();
      const subtotalTotal = String(activeRepairOrder?.subtotal_total || '').trim();
      const taxesTotal = String(activeRepairOrder?.taxes_total || '').trim();
      const paidTotal = String(activeRepairOrder?.paid_total_display || activeRepairOrder?.paid_total || activeRepairOrder?.prepayment_display || activeRepairOrder?.prepayment || '').trim();
      const reason = aiWallDigestShortText(activeRepairOrder?.reason || sourceCard?.description || '', activeRepairOrder?.reason || sourceCard?.description || '');
      const comment = aiWallDigestShortText(activeRepairOrder?.comment || '', activeRepairOrder?.comment || '');
      const note = aiWallDigestShortText(activeRepairOrder?.note || '', activeRepairOrder?.note || '');
      const works = Array.isArray(activeRepairOrder?.works) ? activeRepairOrder.works : [];
      const materials = Array.isArray(activeRepairOrder?.materials) ? activeRepairOrder.materials : [];
      const payments = Array.isArray(activeRepairOrder?.payments) ? activeRepairOrder.payments : [];
      const workSummary = works.slice(0, 5).map((row) => {
        const label = aiWallDigestShortText(row?.name || row?.title || row?.work_name || '', row?.name || row?.title || row?.work_name || '');
        const quantity = String(row?.quantity || '').trim();
        const total = String(row?.total || '').trim();
        return [label, quantity ? 'x' + quantity : '', total ? 'итого ' + total : ''].filter(Boolean).join(' · ');
      }).filter(Boolean);
      const materialSummary = materials.slice(0, 5).map((row) => {
        const label = aiWallDigestShortText(row?.name || row?.title || row?.work_name || '', row?.name || row?.title || row?.work_name || '');
        const quantity = String(row?.quantity || '').trim();
        const total = String(row?.total || '').trim();
        return [label, quantity ? 'x' + quantity : '', total ? 'итого ' + total : ''].filter(Boolean).join(' · ');
      }).filter(Boolean);
      const aiRelevantFacts = {
        client,
        phone,
        vehicle,
        vin,
        mileage,
        reason,
        comment,
        note,
        works: workSummary,
        materials: materialSummary,
        payments_count: payments.length,
        payment_method: paymentMethod,
        payment_method_label: paymentMethodLabel,
        payment_status: paymentStatus,
        payment_status_label: paymentStatusLabel,
        grand_total: grandTotal,
        due_total: dueTotal,
        subtotal_total: subtotalTotal,
        taxes_total: taxesTotal,
        paid_total: paidTotal,
      };
      const keyFields = [
        number ? { key: 'number', label: 'Номер', value: number } : null,
        status ? { key: 'status', label: 'Статус', value: statusLabel ? status + ' · ' + statusLabel : status } : null,
        client ? { key: 'client', label: 'Клиент', value: client } : null,
        vehicle ? { key: 'vehicle', label: 'Машина', value: vehicle } : null,
        vehicleId ? { key: 'vehicle_id', label: 'ID машины', value: vehicleId } : null,
        licensePlate ? { key: 'license_plate', label: 'Номерной знак', value: licensePlate } : null,
        vin ? { key: 'vin', label: 'VIN', value: vin } : null,
        mileage ? { key: 'mileage', label: 'Пробег', value: mileage } : null,
        paymentStatusLabel || paymentStatus ? { key: 'payment_status', label: 'Платежный статус', value: paymentStatusLabel || paymentStatus } : null,
        grandTotal ? { key: 'grand_total', label: 'Итог', value: grandTotal } : null,
        dueTotal ? { key: 'due_total', label: 'Остаток', value: dueTotal } : null,
      ].filter(Boolean);
      const summaryLabel = [number || repairOrderId || 'RO', vehicle, client || statusLabel || status].filter(Boolean).join(' · ');
      return {
        kind: 'repair_order_context',
        source_kind: activeRepairOrder ? 'repair_order' : 'workspace',
        repair_order_id: repairOrderId,
        repair_order_number: number,
        repair_order_status: status,
        repair_order_status_label: statusLabel,
        summary_label: summaryLabel,
        key_fields: aiCompactTrimList(keyFields, AI_COMPACT_CONTEXT_LIMITS.repair_key_fields),
        ai_relevant_facts: aiRelevantFacts,
        work_summary: aiCompactTrimList(workSummary, AI_COMPACT_CONTEXT_LIMITS.repair_work_summary),
        material_summary: aiCompactTrimList(materialSummary, AI_COMPACT_CONTEXT_LIMITS.repair_material_summary),
        notes_summary: aiCompactTrimList([reason, comment, note].filter(Boolean), 3),
        payment_summary: {
          payment_method: paymentMethod,
          payment_method_label: paymentMethodLabel,
          payment_status: paymentStatus,
          payment_status_label: paymentStatusLabel,
          paid_total: paidTotal,
          subtotal_total: subtotalTotal,
          taxes_total: taxesTotal,
          grand_total: grandTotal,
          due_total: dueTotal,
        },
        client_label: client,
        vehicle_label: vehicle,
        vehicle_id: vehicleId,
        license_plate: licensePlate,
        attached_card_id: String(sourceCard?.id || '').trim(),
        wall_digest_label: String(digest?.summary_label || digest?.label || '').trim(),
        wall_digest_summary: String(digest?.summary_text || '').trim(),
        wall_digest_key_facts: aiCompactTrimList(Array.isArray(digest?.key_facts) ? digest.key_facts : [], 4),
        wall_digest_notable_changes: aiCompactTrimList(Array.isArray(digest?.notable_changes) ? digest.notable_changes : [], 3),
      };
    }

    function buildAiAttachmentIntakePacket(card = state.activeCard, repairOrder = aiChatActiveRepairOrderScope(), wallDigest = null) {
      const sourceCard = card && typeof card === 'object' ? card : null;
      const sourceRepairOrder = repairOrder && typeof repairOrder === 'object' ? repairOrder : null;
      const digest = wallDigest && typeof wallDigest === 'object'
        ? wallDigest
        : (state.aiCompactContext?.wall_digest && typeof state.aiCompactContext.wall_digest === 'object'
          ? state.aiCompactContext.wall_digest
          : buildAiWallDigestPacket());
      const cardAttachments = Array.isArray(sourceCard?.attachments) ? sourceCard.attachments : [];
      const repairOrderAttachments = Array.isArray(sourceRepairOrder?.attachments) ? sourceRepairOrder.attachments : [];
      const sourceAttachments = [];
      cardAttachments.forEach((attachment) => {
        if (!attachment || typeof attachment !== 'object' || attachment.removed) return;
        sourceAttachments.push({
          source_kind: 'card',
          source_id: String(sourceCard?.id || '').trim(),
          attachment: attachment,
        });
      });
      repairOrderAttachments.forEach((attachment) => {
        if (!attachment || typeof attachment !== 'object' || attachment.removed) return;
        sourceAttachments.push({
          source_kind: 'repair_order',
          source_id: String(sourceRepairOrder?.id || sourceRepairOrder?.number || '').trim(),
          attachment: attachment,
        });
      });
      const normalizedItems = sourceAttachments.slice(0, AI_COMPACT_CONTEXT_LIMITS.attachment_items).map((item) => {
        const attachment = item.attachment && typeof item.attachment === 'object' ? item.attachment : {};
        const fileName = String(attachment.file_name || attachment.name || '').trim();
        const mimeType = normalizeAttachmentMimeType(attachment.mime_type || attachment.type || '');
        const fileExtension = attachmentExtension(fileName);
        const fileTypeLabel = mimeType
          ? mimeType
          : (fileExtension ? attachmentMimeTypeFromExtension(fileExtension) : '');
        const contentHint = aiCompactTrimText([
          fileExtension ? fileExtension.replace(/^\\./, '').toUpperCase() : '',
          mimeType.startsWith('image/') ? 'IMAGE' : '',
          mimeType === 'application/pdf' ? 'PDF' : '',
          attachment.size_bytes !== undefined ? formatBytes(Number(attachment.size_bytes || 0)) : '',
        ].filter(Boolean).join(' · '), '', AI_COMPACT_CONTEXT_LIMITS.attachment_label);
        const aiReady = Boolean(fileName || fileTypeLabel || attachment.size_bytes !== undefined);
        return {
          attachment_id: String(attachment.id || '').trim(),
          source_kind: item.source_kind,
          source_id: item.source_id,
          file_name: fileName,
          file_type: fileTypeLabel || mimeType || 'application/octet-stream',
          file_extension: fileExtension,
          content_hint: contentHint || (aiReady ? 'AI-READY' : 'NOT READY'),
          ai_ready: aiReady,
          has_text_hint: Boolean(fileExtension && ['.txt', '.doc', '.docx', '.pdf'].includes(fileExtension.toLowerCase()) || String(fileName || '').toLowerCase().includes('scan') || String(fileName || '').toLowerCase().includes('photo')),
          size_bytes: Number(attachment.size_bytes ?? 0) || 0,
          created_at: String(attachment.created_at || attachment.created || '').trim(),
        };
      });
      const cardCount = cardAttachments.filter((attachment) => attachment && typeof attachment === 'object' && !attachment.removed).length;
      const repairOrderCount = repairOrderAttachments.filter((attachment) => attachment && typeof attachment === 'object' && !attachment.removed).length;
      const label = normalizedItems.length
        ? 'ВЛОЖЕНИЯ · ' + normalizedItems.length + ' ГОТОВЫ'
        : 'ВЛОЖЕНИЯ · НЕ ДОСТУПНЫ';
      return {
        kind: 'attachments_intake',
        source_kind: sourceAttachments.length ? (sourceCard?.id ? 'card' : 'repair_order') : 'workspace',
        ready: Boolean(sourceAttachments.length),
        card_attachment_count: cardCount,
        repair_order_attachment_count: repairOrderCount,
        total_attachment_count: normalizedItems.length,
        label: aiCompactTrimText(label, label, AI_COMPACT_CONTEXT_LIMITS.attachment_label),
        summary_label: aiCompactTrimText(label, label, AI_COMPACT_CONTEXT_LIMITS.attachment_label),
        items: normalizedItems,
        card_attachment_ids: aiCompactTrimList(normalizedItems.filter((item) => item.source_kind === 'card').map((item) => item.attachment_id).filter(Boolean), AI_COMPACT_CONTEXT_LIMITS.attachment_ids),
        repair_order_attachment_ids: aiCompactTrimList(normalizedItems.filter((item) => item.source_kind === 'repair_order').map((item) => item.attachment_id).filter(Boolean), AI_COMPACT_CONTEXT_LIMITS.attachment_ids),
        attachment_sources: {
          card: cardCount,
          repair_order: repairOrderCount,
        },
        wall_digest_label: String(digest?.summary_label || digest?.label || '').trim(),
      };
    }

    function buildAiCompactContextPacket() {
      const card = state.activeCard && typeof state.activeCard === 'object' ? state.activeCard : null;
      const currentCard = card ? {
        id: String(card.id || '').trim(),
        title: String(card.title || '').trim(),
        vehicle: String(card.vehicle || '').trim(),
        column: String(card.column || '').trim(),
        status: String(card.status || '').trim(),
      } : null;
      const repairOrder = aiChatActiveRepairOrderScope();
      const repairOrderId = String(repairOrder?.id || repairOrder?.number || '').trim();
      const cardLabel = aiChatContextCardLabel(card);
      const repairOrderLabel = aiChatContextRepairOrderLabel(repairOrder);
      const wall = state.gptWall && typeof state.gptWall === 'object' ? state.gptWall : null;
      const wallDigest = buildAiWallDigestPacket(wall);
      const cardContext = buildAiCardContextPacket(card, repairOrder, wallDigest);
      const repairOrderContext = buildAiRepairOrderContextPacket(repairOrder, card, wallDigest);
      const attachmentsIntake = buildAiAttachmentIntakePacket(card, repairOrder, wallDigest);
      const wallView = normalizeGptWallView(state.gptWallView);
      const wallMeta = wall?.meta && typeof wall.meta === 'object' ? wall.meta : {};
      const wallLabel = wall
        ? 'СТЕНА · ' + (wallView === 'event_log' ? 'ЖУРНАЛ СОБЫТИЙ' : 'СОДЕРЖАНИЕ ДОСКИ')
        : 'СТЕНА · НЕ ЗАГРУЖЕНА';
      const packetDraft = {
        kind: 'compact_context',
        surface: 'ai_chat',
        source_kind: currentCard ? 'card' : 'workspace',
        card_id: String(currentCard?.id || '').trim(),
        card_label: cardLabel,
        card_scope: currentCard,
        repair_order_id: repairOrderId,
        repair_order_label: repairOrderLabel,
        repair_order_scope: repairOrder && typeof repairOrder === 'object' ? repairOrder : null,
        card_context: cardContext,
        repair_order_context: repairOrderContext,
        repair_order_context_label: String(repairOrderContext?.summary_label || repairOrderLabel || '').trim(),
        wall_context: {
          kind: 'wall',
          source_kind: wall ? 'gpt_wall' : 'none',
          has_wall: Boolean(wall),
          view: wallView,
          status: String(wallMeta.status || '').trim(),
          revision: String(wallMeta.revision || '').trim(),
          label: wallLabel,
        },
        wall_digest: wallDigest,
        attachments_intake: attachmentsIntake,
        context_label: repairOrderLabel ? cardLabel + ' · ' + repairOrderLabel : cardLabel,
        has_card_scope: Boolean(currentCard?.id),
        has_repair_order_scope: Boolean(repairOrderId),
      };
      const cache = state.aiCompactContextCache && typeof state.aiCompactContextCache === 'object'
        ? state.aiCompactContextCache
        : { signature: '', packet: null };
      const signature = aiCompactContextPacketSignature(packetDraft);
      if (cache.signature === signature && cache.packet && typeof cache.packet === 'object') {
        return cache.packet;
      }
      state.aiCompactContextCache = { signature, packet: packetDraft };
      return packetDraft;
    }

    function aiChatCompactContextSummary(context = state.aiCompactContext) {
      const packet = context && typeof context === 'object' ? context : buildAiCompactContextPacket();
      const lines = [
        packet.card_context?.summary_label || packet.card_label || 'AI-ЧАТ · СВОБОДНАЯ СЕССИЯ',
        packet.card_context?.key_fields?.length ? packet.card_context.key_fields.slice(0, 3).map((item) => item.value).join(' · ') : '',
        packet.card_context?.ai_relevant_facts?.client ? 'Клиент: ' + packet.card_context.ai_relevant_facts.client : '',
        packet.card_context?.ai_relevant_facts?.machine ? 'Машина: ' + packet.card_context.ai_relevant_facts.machine : '',
        packet.card_context?.ai_relevant_facts?.symptoms ? 'Симптомы: ' + packet.card_context.ai_relevant_facts.symptoms : '',
        packet.repair_order_context?.summary_label || packet.repair_order_label ? 'Заказ-наряд: ' + (packet.repair_order_context?.summary_label || packet.repair_order_label) : 'Заказ-наряд: не привязан.',
        packet.wall_digest?.label || packet.wall_context?.label || 'СТЕНА · НЕ ЗАГРУЖЕНА',
        packet.wall_digest?.key_facts?.length ? packet.wall_digest.key_facts.slice(0, 3).join(' · ') : '',
        packet.wall_digest?.notable_changes?.length ? packet.wall_digest.notable_changes.slice(0, 2).join(' · ') : '',
        packet.attachments_intake?.label || 'ВЛОЖЕНИЯ · НЕ ДОСТУПНЫ',
        packet.attachments_intake?.items?.length ? 'Вложения AI: ' + packet.attachments_intake.items.slice(0, 3).map((item) => item.file_name || item.content_hint || item.attachment_id || 'attachment').join(' · ') : '',
      ];
      return lines.join('\\n');
    }

    function refreshAiCompactContextPacket() {
      state.aiCompactContext = buildAiCompactContextPacket();
      state.aiChatWindowContext = buildAiChatWindowContext();
      state.aiSurfaceContext = state.aiChatWindowContext;
      return state.aiCompactContext;
    }

    function aiChatMessageTone(role, message) {
      if (message && typeof message === 'object' && String(message.state || '').trim()) {
        return String(message.state).trim().toLowerCase();
      }
      if (role === 'assistant') return 'online';
      if (role === 'system' || role === 'status') return 'idle';
      return 'online';
    }

    function aiChatMessageTitle(role) {
      if (role === 'assistant') return 'AI';
      if (role === 'system') return 'SYSTEM';
      if (role === 'status') return 'STATUS';
      return 'YOU';
    }

    function aiChatKnowledgeSourceLabels(knowledge = state.aiChatWindowKnowledge) {
      const packet = knowledge && typeof knowledge === 'object' ? knowledge : null;
      const labels = Array.isArray(packet?.source_labels) ? packet.source_labels.map((item) => String(item || '').trim()).filter(Boolean) : [];
      if (!labels.length) labels.push('CRM');
      return labels;
    }

    async function resolveAiChatKnowledge(input, context = state.aiCompactContext) {
      const prompt = String(input || '').trim();
      const payloadContext = context && typeof context === 'object' ? context : buildAiCompactContextPacket();
      const promptProfile = ensureAiChatWindowPromptProfile();
      try {
        const knowledge = await api('/api/get_ai_chat_knowledge', {
          method: 'POST',
          body: {
            prompt,
            context: payloadContext,
            prompt_profile: promptProfile,
            source: 'ai_chat',
          },
        });
        if (knowledge && typeof knowledge === 'object') return knowledge;
      } catch (error) {
        console.warn('ai_chat knowledge lookup failed:', error);
      }
      return {
        kind: 'ai_chat_knowledge',
        prompt,
        source_labels: ['CRM'],
        crm: {
          kind: 'compact_context',
          source_kind: 'fallback',
        },
        documents: {
          available: false,
          requested: false,
          used: false,
          count: 0,
          items: [],
        },
        internet: {
          available: false,
          requested: false,
          used: false,
          count: 0,
          query: prompt,
          allowed_domains: [],
          items: [],
        },
        policy: {
          external_knowledge_used: false,
          external_knowledge_allowed: false,
          fallback: true,
        },
      };
    }

    function aiChatRenderInlineMarkdown(source) {
      const escaped = escapeHtml(String(source || ''));
      return escaped
        .replace(/\\*\\*([^*]+?)\\*\\*/g, '<strong>$1</strong>')
        .replace(/(^|[^*])\\*([^*\\n]+?)\\*(?!\\*)/g, '$1<em>$2</em>')
        .replace(/`([^`]+?)`/g, '<code>$1</code>')
        .replace(/\\[([^\\]]+?)\\]\\((https?:\\/\\/[^\\s)]+)\\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
    }

    function aiChatRenderPlainText(source) {
      return '<p>' + escapeHtml(String(source || '').trim()).replace(/\\n/g, '<br>') + '</p>';
    }

    function aiChatRenderMarkdown(source) {
      const input = String(source || '').replace(/\\r\\n/g, '\\n');
      const lines = input.split('\\n');
      const blocks = [];
      let paragraph = [];
      let listItems = [];
      let listType = '';
      let codeLines = [];
      let inCode = false;

      function flushParagraph() {
        if (!paragraph.length) return;
        blocks.push('<p>' + aiChatRenderInlineMarkdown(paragraph.join(' ').trim()) + '</p>');
        paragraph = [];
      }

      function flushList() {
        if (!listItems.length) return;
        const tag = listType === 'ol' ? 'ol' : 'ul';
        blocks.push('<' + tag + '>' + listItems.map((item) => '<li>' + aiChatRenderInlineMarkdown(item) + '</li>').join('') + '</' + tag + '>');
        listItems = [];
        listType = '';
      }

      function flushCode() {
        if (!codeLines.length) return;
        blocks.push('<pre><code>' + escapeHtml(codeLines.join('\\n')) + '</code></pre>');
        codeLines = [];
      }

      function flushAllText() {
        flushParagraph();
        flushList();
        flushCode();
      }

      for (const rawLine of lines) {
        const line = String(rawLine || '');
        const trimmed = line.trim();
        if (trimmed.startsWith('```')) {
          if (inCode) {
            flushCode();
            inCode = false;
          } else {
            flushParagraph();
            flushList();
            inCode = true;
          }
          continue;
        }
        if (inCode) {
          codeLines.push(line);
          continue;
        }
        if (!trimmed) {
          flushAllText();
          continue;
        }
        const bulletMatch = line.match(/^\\s*[-*]\\s+(.+)$/);
        if (bulletMatch) {
          flushParagraph();
          if (listType && listType !== 'ul') flushList();
          listType = 'ul';
          listItems.push(bulletMatch[1]);
          continue;
        }
        const orderedMatch = line.match(/^\\s*\\d+\\.\\s+(.+)$/);
        if (orderedMatch) {
          flushParagraph();
          if (listType && listType !== 'ol') flushList();
          listType = 'ol';
          listItems.push(orderedMatch[1]);
          continue;
        }
        flushList();
        paragraph.push(line);
      }

      if (inCode) flushCode();
      flushAllText();
      if (!blocks.length) {
        return aiChatRenderPlainText(input);
      }
      return blocks.join('');
    }

    function aiChatRenderMessageBody(message) {
      const normalizedRole = normalizeAiChatMessageRole(message?.role);
      const content = String(message?.text || '').trim();
      if (!content) return '<p>...</p>';
      if (normalizedRole === 'assistant' || normalizedRole === 'system' || normalizedRole === 'status') {
        return aiChatRenderMarkdown(content);
      }
      return aiChatRenderPlainText(content);
    }

    async function aiChatBuildAssistantResponse(input) {
      const context = state.aiCompactContext && typeof state.aiCompactContext === 'object'
        ? state.aiCompactContext
        : buildAiCompactContextPacket();
      const profile = ensureAiChatWindowPromptProfile();
      const prompt = String(input || '').trim();
      const knowledge = await resolveAiChatKnowledge(prompt, context);
      state.aiChatWindowKnowledge = knowledge;
      const sourceLabels = aiChatKnowledgeSourceLabels(knowledge);
      const documentTitles = Array.isArray(knowledge?.documents?.items)
        ? knowledge.documents.items.slice(0, 3).map((item) => String(item?.title || item?.document_id || 'document').trim()).filter(Boolean)
        : [];
      const internetTitles = Array.isArray(knowledge?.internet?.items)
        ? knowledge.internet.items.slice(0, 3).map((item) => String(item?.title || item?.domain || item?.url || 'internet result').trim()).filter(Boolean)
        : [];
      const responseParts = [
        'Принял запрос для AI-чата.',
        prompt ? 'Запрос: ' + prompt : 'Запрос пустой.',
        'Источники: ' + sourceLabels.join(' · '),
        context.card_context?.summary_label ? 'Карточка: ' + context.card_context.summary_label : (context.card_label ? 'Карточка: ' + context.card_label : 'Карточка: нет активного scope.'),
        context.card_context?.ai_relevant_facts?.client ? 'Клиент: ' + context.card_context.ai_relevant_facts.client : '',
        context.card_context?.ai_relevant_facts?.machine ? 'Машина: ' + context.card_context.ai_relevant_facts.machine : '',
        context.card_context?.ai_relevant_facts?.symptoms ? 'Симптомы: ' + context.card_context.ai_relevant_facts.symptoms : '',
        context.card_context?.ai_relevant_facts?.works?.length ? 'Работы: ' + context.card_context.ai_relevant_facts.works.slice(0, 3).join(' · ') : '',
        context.card_context?.ai_relevant_facts?.notes?.length ? 'Заметки: ' + context.card_context.ai_relevant_facts.notes.slice(0, 2).join(' · ') : '',
        context.repair_order_context?.summary_label ? 'Заказ-наряд: ' + context.repair_order_context.summary_label : (context.repair_order_label ? 'Заказ-наряд: ' + context.repair_order_label : 'Заказ-наряд: не привязан.'),
        context.repair_order_context?.ai_relevant_facts?.payment_status_label ? 'Платежный статус: ' + context.repair_order_context.ai_relevant_facts.payment_status_label : '',
        context.repair_order_context?.ai_relevant_facts?.grand_total ? 'Итог: ' + context.repair_order_context.ai_relevant_facts.grand_total : '',
        context.repair_order_context?.ai_relevant_facts?.due_total ? 'Остаток: ' + context.repair_order_context.ai_relevant_facts.due_total : '',
        context.repair_order_context?.work_summary?.length ? 'Работы ЗН: ' + context.repair_order_context.work_summary.slice(0, 3).join(' · ') : '',
        context.repair_order_context?.material_summary?.length ? 'Материалы ЗН: ' + context.repair_order_context.material_summary.slice(0, 3).join(' · ') : '',
        context.wall_digest?.label ? 'Контекст стены: ' + context.wall_digest.label : (context.wall_context?.label ? 'Контекст стены: ' + context.wall_context.label : ''),
        context.wall_digest?.key_facts?.length ? 'Ключевые факты стены: ' + context.wall_digest.key_facts.slice(0, 3).join(' · ') : '',
        context.wall_digest?.notable_changes?.length ? 'Последние изменения: ' + context.wall_digest.notable_changes.slice(0, 2).join(' · ') : '',
        context.attachments_intake?.label ? 'Вложенная зона: ' + context.attachments_intake.label : '',
        context.attachments_intake?.items?.length ? 'Вложения AI: ' + context.attachments_intake.items.slice(0, 3).map((item) => item.file_name || item.content_hint || item.attachment_id || 'attachment').join(' · ') : '',
        documentTitles.length ? 'Документы: ' + documentTitles.join(' · ') : '',
        internetTitles.length ? 'Интернет: ' + internetTitles.join(' · ') : '',
        'Compact context: ' + aiChatCompactContextSummary(context).replace(/\\n/g, ' · '),
        profile.user_tune ? 'Пользовательская настройка: ' + profile.user_tune : '',
      ].filter(Boolean);
      const bulletLine = [
        context.card_context?.card_id ? '- card context available' : '- card context unavailable',
        context.repair_order_context?.repair_order_id ? '- repair order context available' : '- repair order context unavailable',
        context.wall_digest?.has_wall ? '- wall digest available' : '- wall digest unavailable',
        context.attachments_intake?.ready ? '- attachments intake ready' : '- attachments intake unavailable',
        context.attachments_intake?.items?.length ? '- attachment items available' : '- attachment items unavailable',
        knowledge?.documents?.used ? '- documents lookup used' : '- documents lookup skipped',
        knowledge?.internet?.used ? '- internet lookup used' : '- internet lookup skipped',
      ];
      return responseParts.join('\\n') + '\\n\\n' + bulletLine.join('\\n');
    }

    function renderAiChatWindowHistory() {
      hydrateAiChatWindowUiRefs();
      ensureAiChatWindowHistory();
      if (!els.aiChatWindowMessages) return;
      els.aiChatWindowMessages.innerHTML = state.aiChatWindowHistory.map((message) => {
        const normalizedRole = normalizeAiChatMessageRole(message?.role);
        const tone = aiChatMessageTone(normalizedRole, message);
        const title = aiChatMessageTitle(normalizedRole);
        const text = aiChatRenderMessageBody(message);
        const metaLine = [];
        if (message?.created_at) metaLine.push(escapeHtml(String(message.created_at)));
        if (message?.context?.kind) metaLine.push(escapeHtml(String(message.context.kind)));
        if (message?.context?.card_label) metaLine.push(escapeHtml(String(message.context.card_label)));
        if (message?.context?.repair_order_label) metaLine.push(escapeHtml(String(message.context.repair_order_label)));
        if (message?.context?.repair_order_context_label) metaLine.push(escapeHtml(String(message.context.repair_order_context_label)));
        const knowledgeSourceLabels = Array.isArray(message?.context?.knowledge_source_labels) && message.context.knowledge_source_labels.length
          ? message.context.knowledge_source_labels
          : (Array.isArray(message?.knowledge_source_labels) ? message.knowledge_source_labels : []);
        if (knowledgeSourceLabels.length) metaLine.push(escapeHtml('Источники: ' + knowledgeSourceLabels.join(' · ')));
        if (message?.source) metaLine.push(escapeHtml(String(message.source)));
        return '<article class="ai-chat-window__message" data-role="' + escapeHtml(normalizedRole) + '" data-tone="' + escapeHtml(tone) + '" data-message-id="' + escapeHtml(message?.id || '') + '">' +
          '<div class="ai-chat-window__message-title">' + title + '</div>' +
          '<div class="ai-chat-window__message-text">' + text + '</div>' +
          (metaLine.length ? '<div class="ai-chat-window__message-meta">' + metaLine.join(' · ') + '</div>' : '') +
        '</article>';
      }).join('');
      requestAnimationFrame(() => {
        if (els.aiChatWindowMessages) {
          els.aiChatWindowMessages.scrollTop = els.aiChatWindowMessages.scrollHeight;
        }
      });
    }

    function appendAiChatWindowMessage(role, text, meta = {}) {
      ensureAiChatWindowHistory();
      const message = createAiChatMessage(role, text, meta);
      state.aiChatWindowHistory.push(message);
      renderAiChatWindowHistory();
      return message;
    }

    function focusAiChatWindowInput() {
      hydrateAiChatWindowUiRefs();
      if (!els.aiChatWindowInput) return;
      try {
        els.aiChatWindowInput.focus({ preventScroll: true });
      } catch (error) {
        els.aiChatWindowInput.focus();
      }
    }

    async function handleAiChatWindowSend() {
      hydrateAiChatWindowUiRefs();
      const input = String(els.aiChatWindowInput?.value || '').trim();
      if (!input) return;
      state.aiChatWindowKnowledge = null;
      appendAiChatWindowMessage('user', input, { kind: 'user_input', source: 'composer' });
      if (els.aiChatWindowInput) els.aiChatWindowInput.value = '';
      try {
        const response = await aiChatBuildAssistantResponse(input);
        appendAiChatWindowMessage('assistant', response, {
          kind: 'scoped_runtime',
          source: 'ai_chat',
          topic: 'reply',
          knowledge_source_labels: aiChatKnowledgeSourceLabels(),
        });
      } catch (error) {
        appendAiChatWindowMessage('status', 'Ошибка knowledge/chat слоя: ' + String(error?.message || error || 'неизвестная ошибка'), {
          kind: 'error',
          source: 'ai_chat',
          state: 'error',
        });
      } finally {
        state.aiChatWindowHistoryContext = aiChatHistoryContextSnapshot();
        focusAiChatWindowInput();
      }
    }

    function handleAiChatWindowInputKeydown(event) {
      if (!(event.ctrlKey || event.metaKey) || event.key !== 'Enter') return;
      event.preventDefault();
      void handleAiChatWindowSend();
    }

    function handleAiChatWindowPromptProfileInput(event) {
      const profile = ensureAiChatWindowPromptProfile();
      profile.user_tune = String(event?.target?.value || '').replace(/\\r\\n/g, '\\n');
      state.aiChatWindowPromptProfile = profile;
      state.aiChatWindowHistoryContext = aiChatHistoryContextSnapshot();
    }

    function handleAiChatWindowSettingsToggle() {
      state.aiChatWindowSettingsOpen = !Boolean(state.aiChatWindowSettingsOpen);
      renderAiChatWindowSettings();
    }

    function renderAiChatWindowSettings() {
      hydrateAiChatWindowUiRefs();
      const profile = ensureAiChatWindowPromptProfile();
      const isOpen = Boolean(state.aiChatWindowSettingsOpen);
      if (els.aiChatWindowSettingsPane) {
        els.aiChatWindowSettingsPane.hidden = !isOpen;
        els.aiChatWindowSettingsPane.classList.toggle('is-open', isOpen);
      }
      if (els.aiChatWindowSettingsButton) {
        els.aiChatWindowSettingsButton.disabled = false;
        els.aiChatWindowSettingsButton.dataset.state = isOpen ? 'open' : 'closed';
        els.aiChatWindowSettingsButton.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
        els.aiChatWindowSettingsButton.textContent = isOpen ? 'НАСТРОЙКИ ?' : 'НАСТРОЙКИ';
      }
      if (els.aiChatWindowPromptSystem) {
        els.aiChatWindowPromptSystem.textContent = profile.system_instruction;
      }
      if (els.aiChatWindowPromptResponse) {
        els.aiChatWindowPromptResponse.textContent = profile.response_profile;
      }
      if (els.aiChatWindowPromptProfileInput && els.aiChatWindowPromptProfileInput.value !== profile.user_tune) {
        els.aiChatWindowPromptProfileInput.value = profile.user_tune;
      }
    }

    function renderAiChatWindowContext() {
      hydrateAiChatWindowUiRefs();
      const context = state.aiChatWindowContext && typeof state.aiChatWindowContext === 'object'
        ? state.aiChatWindowContext
        : buildAiChatWindowContext();
      const label = String(context.context_label || aiChatContextCardLabel()).trim();
      const repairOrderLabel = String(context.repair_order_label || '').trim();
      const sourceKind = String(context.source_kind || 'workspace').trim().toLowerCase();
      const subtitleParts = [
        'Отдельный рабочий AI surface для будущего чата.',
        context.card_id ? 'Card #' + context.card_id : 'Без карточки',
        repairOrderLabel ? repairOrderLabel : 'Без заказа-наряда',
      ].filter(Boolean);
      if (els.aiChatWindowContextLabel) {
        els.aiChatWindowContextLabel.textContent = label;
        els.aiChatWindowContextLabel.dataset.kind = sourceKind;
      }
      if (els.aiChatWindowSubtitle) {
        const compactTail = [
          context.card_context?.summary_label || context.card_label || '',
          context.card_context?.ai_relevant_facts?.client || '',
          context.repair_order_context?.summary_label || context.repair_order_label || '',
          context.repair_order_context?.ai_relevant_facts?.payment_status_label || '',
          context.wall_digest?.label || context.wall_context?.label || '',
          context.wall_digest?.key_facts?.slice(0, 2).join(' · ') || '',
          context.attachments_intake?.label || '',
        ].filter(Boolean).join(' · ');
        els.aiChatWindowSubtitle.textContent = [subtitleParts.join(' · '), compactTail].filter(Boolean).join(' · ');
      }
    }

    function bindAiChatWindowUiEvents() {
      if (state.aiChatWindowUiBound) return;
      hydrateAiChatWindowUiRefs();
      els.aiChatWindowSettingsButton?.addEventListener('click', handleAiChatWindowSettingsToggle);
      els.aiChatWindowSendButton?.addEventListener('click', handleAiChatWindowSend);
      els.aiChatWindowInput?.addEventListener('keydown', handleAiChatWindowInputKeydown);
      els.aiChatWindowPromptProfileInput?.addEventListener('input', handleAiChatWindowPromptProfileInput);
      state.aiChatWindowUiBound = true;
    }

    function hydrateAgentTasksUiRefs() {
      els.agentTasksModal = document.getElementById('agentTasksModal');
      els.agentTasksMeta = document.getElementById('agentTasksMeta');
      els.agentTasksList = document.getElementById('agentTasksList');
      els.agentTasksNewButton = document.getElementById('agentTasksNewButton');
      els.agentTasksEditorTitle = document.getElementById('agentTasksEditorTitle');
      els.agentTaskNameInput = document.getElementById('agentTaskNameInput');
      els.agentTaskPromptInput = document.getElementById('agentTaskPromptInput');
      els.agentTaskScopeTypeInput = document.getElementById('agentTaskScopeTypeInput');
      els.agentTaskScopeColumnInput = document.getElementById('agentTaskScopeColumnInput');
      els.agentTaskScheduleTypeInput = document.getElementById('agentTaskScheduleTypeInput');
      els.agentTaskIntervalValueInput = document.getElementById('agentTaskIntervalValueInput');
      els.agentTaskIntervalUnitInput = document.getElementById('agentTaskIntervalUnitInput');
      els.agentTaskActiveInput = document.getElementById('agentTaskActiveInput');
      els.agentTaskFormMeta = document.getElementById('agentTaskFormMeta');
      els.agentTaskSaveButton = document.getElementById('agentTaskSaveButton');
      els.agentTaskRunButton = document.getElementById('agentTaskRunButton');
      els.agentTaskResetButton = document.getElementById('agentTaskResetButton');
    }

    function bindAgentUiEvents() {
      if (state.agentUiBound) return;
      hydrateAgentUiRefs();
      els.agentQuickActions?.addEventListener('click', handleAgentQuickActionClick);
      els.agentTasksOpenButton?.addEventListener('click', openAgentTasksModal);
      els.agentAutofillButton?.addEventListener('click', toggleAgentCardAutofill);
      els.agentAutofillPromptToggle?.addEventListener('click', toggleAgentAutofillPromptPanel);
      els.agentAutofillPromptSaveButton?.addEventListener('click', saveAgentAutofillPrompt);
      els.agentAutofillPromptResetButton?.addEventListener('click', resetAgentAutofillPrompt);
      els.agentRunsList?.addEventListener('click', handleAgentRunSelection);
      els.agentRunButton?.addEventListener('click', enqueueAgentTask);
      els.agentTaskInput?.addEventListener('input', syncAgentTaskInputHeight);
      els.agentTaskInput?.addEventListener('keydown', (event) => {
        if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
          event.preventDefault();
          enqueueAgentTask();
        }
      });
      els.agentModal?.addEventListener('click', handleAgentModalOverlayClick);
      els.agentResultPanel?.addEventListener('click', handleAgentResultActionClick);
      state.agentUiBound = true;
    }

    function bindAgentTasksUiEvents() {
      if (state.agentTasksUiBound) return;
      hydrateAgentTasksUiRefs();
      els.agentTasksNewButton?.addEventListener('click', resetAgentScheduledTaskForm);
      els.agentTasksList?.addEventListener('click', handleAgentScheduledTasksListClick);
      els.agentTaskScopeTypeInput?.addEventListener('change', syncAgentScheduledTaskFormUi);
      els.agentTaskScheduleTypeInput?.addEventListener('change', syncAgentScheduledTaskFormUi);
      els.agentTaskSaveButton?.addEventListener('click', saveAgentScheduledTask);
      els.agentTaskRunButton?.addEventListener('click', runActiveAgentScheduledTask);
      els.agentTaskResetButton?.addEventListener('click', resetAgentScheduledTaskForm);
      state.agentTasksUiBound = true;
    }

    function bindEmployeesUiEvents() {
      if (state.employeesUiBound) return;
      hydrateEmployeesUiRefs();
      els.employeesCreateButton?.addEventListener('click', resetEmployeeForm);
      els.employeeSaveButton?.addEventListener('click', saveEmployee);
      els.employeeDeleteButton?.addEventListener('click', deleteEmployee);
      els.employeeSalaryModeInput?.addEventListener('change', syncEmployeeSalaryModeUi);
      els.employeesMonthInput?.addEventListener('change', () => loadEmployeesWorkspace(false).catch((error) => setStatus(error.message, true)));
      els.employeesReportTabs?.addEventListener('click', handleEmployeesReportTabClick);
      els.employeesSearchInput?.addEventListener('input', handleEmployeesSearchInput);
      els.employeesVisibilityFilters?.addEventListener('click', handleEmployeesVisibilityFilterClick);
      els.employeesList?.addEventListener('click', handleEmployeesListClick);
      els.employeesSummaryTable?.addEventListener('click', handleEmployeesListClick);
      els.employeesDetailTable?.addEventListener('click', handleEmployeesDetailClick);
      els.employeesModal?.addEventListener('input', handleEmployeesModalFormInput);
      els.employeesModal?.addEventListener('change', handleEmployeesModalFormInput);
      els.employeesModal?.addEventListener('click', handleEmployeesModalOverlayClick);
      state.employeesUiBound = true;
    }

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
      state.archiveCards = [];
      state.archiveLoaded = false;
      setOperatorSessionToken('');
      applyBoardScalePreference({ fallbackValue: 1, syncInput: true, persistFallback: false });
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
      applyBoardScalePreference({ fallbackValue: state.snapshot?.settings?.board_scale ?? state.boardScale ?? 1, syncInput: true, persistFallback: true });
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

    async function openArchiveModal() {
      await loadArchive(true);
    }

    function closeNamedModal(closeKey) {
      const closeActions = {
        card: () => closeCardModal(),
        archive: () => {
          els.archiveModal.classList.remove('is-open');
          state.archiveCards = [];
          state.archiveLoaded = false;
        },
        'repair-orders': () => els.repairOrdersModal.classList.remove('is-open'),
        cashboxes: () => els.cashboxesModal.classList.remove('is-open'),
        'cashbox-transfer': () => els.cashboxTransferModal.classList.remove('is-open'),
        employees: () => {
          if (!confirmDiscardEmployeeChanges()) return;
          els.employeesModal?.classList.remove('is-open');
        },
        agent: () => closeAgentModal(),
        'ai-surface': () => closeAiSurface(),
        'ai-chat': () => closeAiChatWindow(),
        'agent-tasks': () => closeAgentTasksModal(),
        wall: () => els.gptWallModal.classList.remove('is-open'),
        settings: () => els.boardSettingsModal.classList.remove('is-open'),
        sticky: () => closeStickyModal(),
        'repair-order': () => closeRepairOrderModal(),
        'repair-order-payments': () => closeRepairOrderPaymentsModal(),
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

    function archivedCardsTotal() {
      const metaTotal = Number(state.snapshot?.meta?.archived_cards_total ?? NaN);
      if (Number.isFinite(metaTotal) && metaTotal >= 0) return metaTotal;
      return Array.isArray(state.archiveCards) ? state.archiveCards.length : 0;
    }

    async function loadArchive(openModal = false, { force = false } = {}) {
      if (state.archiveLoading) {
        const pending = state.archiveLoading;
        await pending;
        if (openModal) maybeOpenModal(els.archiveModal, true);
        return pending;
      }
      if (state.archiveLoaded && !force) {
        renderArchive();
        if (openModal) maybeOpenModal(els.archiveModal, true);
        return { cards: state.archiveCards };
      }
      if (openModal) maybeOpenModal(els.archiveModal, true);
      if (els.archiveList && (!state.archiveLoaded || force)) {
        els.archiveList.innerHTML = '<div class="log-row__meta">ЗАГРУЗКА АРХИВА...</div>';
      }
      state.archiveLoading = (async () => {
        try {
          const data = await api('/api/list_archived_cards?limit=' + ARCHIVE_PREVIEW_LIMIT + '&compact=1');
          state.archiveCards = Array.isArray(data?.cards) ? data.cards : [];
          state.archiveLoaded = true;
          renderArchive();
          if (openModal) maybeOpenModal(els.archiveModal, true);
          return data;
        } catch (error) {
          if (els.archiveList) {
            els.archiveList.innerHTML = '<div class="log-row__meta">НЕ УДАЛОСЬ ЗАГРУЗИТЬ АРХИВ.</div>';
          }
          if (openModal) maybeOpenModal(els.archiveModal, true);
          setStatus(error.message, true);
          return null;
        } finally {
          state.archiveLoading = null;
        }
      })();
      return state.archiveLoading;
    }

    function currentPayrollMonthValue() {
      const now = new Date();
      return String(now.getFullYear()) + '-' + String(now.getMonth() + 1).padStart(2, '0');
    }

    function selectedEmployeeRecord() {
      return (state.employees || []).find((item) => item.id === state.activeEmployeeId) || null;
    }

    function normalizeEmployeesVisibilityFilter(value) {
      return String(value || '').trim() === 'all' ? 'all' : 'active';
    }

    function employeeSalaryModeLabel(mode) {
      if (mode === 'salary_only') return 'ОКЛАД';
      if (mode === 'percent_only') return '% ОТ РАБОТ';
      return 'ОКЛАД + %';
    }

    function normalizeEmployeeComparableText(value) {
      return String(value ?? '').trim();
    }

    function normalizeEmployeeComparableNumber(value) {
      const parsed = repairOrderParseNumber(value);
      return parsed === null ? '' : repairOrderNumberToRaw(parsed);
    }

    function employeeComparableSnapshot(employee = null) {
      return {
        name: normalizeEmployeeComparableText(employee?.name),
        position: normalizeEmployeeComparableText(employee?.position),
        salary_mode: normalizeEmployeeComparableText(employee?.salary_mode || 'salary_plus_percent'),
        base_salary: normalizeEmployeeComparableNumber(employee?.base_salary),
        work_percent: normalizeEmployeeComparableNumber(employee?.work_percent),
        note: normalizeEmployeeComparableText(employee?.note),
        is_active: employee ? Boolean(employee.is_active) : true,
      };
    }

    function employeeFormSnapshot() {
      return {
        name: normalizeEmployeeComparableText(els.employeeNameInput?.value),
        position: normalizeEmployeeComparableText(els.employeePositionInput?.value),
        salary_mode: normalizeEmployeeComparableText(els.employeeSalaryModeInput?.value || 'salary_plus_percent'),
        base_salary: normalizeEmployeeComparableNumber(els.employeeBaseSalaryInput?.value),
        work_percent: normalizeEmployeeComparableNumber(els.employeeWorkPercentInput?.value),
        note: normalizeEmployeeComparableText(els.employeeNoteInput?.value),
        is_active: Boolean(els.employeeActiveInput?.checked),
      };
    }

    function employeeFormHasUnsavedChanges() {
      const baseline = state.employeeFormBaseline || employeeComparableSnapshot(selectedEmployeeRecord());
      return JSON.stringify(employeeFormSnapshot()) !== JSON.stringify(baseline);
    }

    function confirmDiscardEmployeeChanges() {
      if (!employeeFormHasUnsavedChanges()) return true;
      return window.confirm('Несохранённые изменения сотрудника будут потеряны. Продолжить?');
    }

    function filteredEmployeesList() {
      const employees = Array.isArray(state.employees) ? state.employees : [];
      const query = String(state.employeesQuery || '').trim().toLocaleLowerCase('ru');
      const visibilityFilter = normalizeEmployeesVisibilityFilter(state.employeesVisibilityFilter);
      const visibleEmployees = employees
        .filter((employee) => {
          if (visibilityFilter === 'active' && !employee?.is_active) return false;
          if (!query) return true;
          const haystack = [
            employee?.name,
            employee?.position,
            employeeSalaryModeLabel(employee?.salary_mode),
          ].join(' ').toLocaleLowerCase('ru');
          return haystack.includes(query);
        })
        .sort((left, right) => {
          const leftActive = left?.is_active ? 1 : 0;
          const rightActive = right?.is_active ? 1 : 0;
          if (leftActive !== rightActive) return rightActive - leftActive;
          return String(left?.name || '').localeCompare(String(right?.name || ''), 'ru');
        });
      const selectedEmployee = selectedEmployeeRecord();
      if (selectedEmployee && !visibleEmployees.some((item) => item?.id === selectedEmployee.id)) {
        visibleEmployees.unshift(selectedEmployee);
      }
      return visibleEmployees;
    }

    function payrollSummaryMap() {
      const rows = Array.isArray(state.payrollReport?.summary) ? state.payrollReport.summary : [];
      return rows.reduce((map, row) => {
        map.set(String(row.employee_id || ''), row);
        return map;
      }, new Map());
    }

    function syncEmployeesVisibilityFilterUi() {
      if (!els.employeesVisibilityFilters) return;
      const activeFilter = normalizeEmployeesVisibilityFilter(state.employeesVisibilityFilter);
      els.employeesVisibilityFilters.querySelectorAll('[data-filter]').forEach((button) => {
        if (!(button instanceof HTMLElement)) return;
        button.classList.toggle('is-active', String(button.dataset.filter || '') === activeFilter);
      });
    }

    function renderEmployeeProfileMeta() {
      if (!els.employeesMeta) return;
      const selectedEmployee = selectedEmployeeRecord();
      const mode = String(els.employeeSalaryModeInput?.value || selectedEmployee?.salary_mode || 'salary_plus_percent').trim();
      const parts = [];
      if (selectedEmployee) {
        parts.push(selectedEmployee.is_active ? 'АКТИВЕН' : 'ВЫКЛ');
      } else {
        parts.push('НОВЫЙ СОТРУДНИК');
      }
      parts.push(employeeSalaryModeLabel(mode));
      if (employeeFormHasUnsavedChanges()) parts.push('ИЗМЕНЕНО');
      els.employeesMeta.textContent = parts.join(' · ');
    }

    function normalizeEmployeesReportTab(value) {
      return String(value || '').trim() === 'details' ? 'details' : 'summary';
    }

    function syncEmployeesReportTabUi() {
      const activeTab = normalizeEmployeesReportTab(state.employeesReportTab);
      if (els.employeesReportTabs) {
        els.employeesReportTabs.querySelectorAll('[data-employees-report-tab]').forEach((button) => {
          if (!(button instanceof HTMLElement)) return;
          const isActive = String(button.dataset.employeesReportTab || '') === activeTab;
          button.classList.toggle('is-active', isActive);
          button.classList.toggle('btn--ghost', !isActive);
        });
      }
      els.employeesSummaryPanel?.classList.toggle('is-active', activeTab === 'summary');
      els.employeesDetailsPanel?.classList.toggle('is-active', activeTab === 'details');
    }

    function syncEmployeeSalaryModeUi() {
      const mode = String(els.employeeSalaryModeInput?.value || 'salary_plus_percent').trim();
      const salaryDisabled = mode === 'percent_only';
      const percentDisabled = mode === 'salary_only';
      if (els.employeeBaseSalaryInput) {
        els.employeeBaseSalaryInput.disabled = salaryDisabled;
      }
      if (els.employeeWorkPercentInput) {
        els.employeeWorkPercentInput.disabled = percentDisabled;
      }
      renderEmployeeProfileMeta();
    }

    function fillEmployeeForm(employee) {
      const current = employee || null;
      if (els.employeesCardMode) {
        els.employeesCardMode.textContent = current ? String(current.name || 'СОТРУДНИК').toUpperCase() : 'НОВЫЙ СОТРУДНИК';
      }
      els.employeeNameInput.value = current?.name || '';
      els.employeePositionInput.value = current?.position || '';
      els.employeeSalaryModeInput.value = current?.salary_mode || 'salary_plus_percent';
      els.employeeBaseSalaryInput.value = current?.base_salary || '';
      els.employeeWorkPercentInput.value = current?.work_percent || '';
      els.employeeNoteInput.value = current?.note || '';
      els.employeeActiveInput.checked = current ? Boolean(current.is_active) : true;
      if (els.employeeNoteDetails) {
        els.employeeNoteDetails.open = Boolean(String(current?.note || '').trim());
      }
      if (els.employeeDeleteButton) {
        els.employeeDeleteButton.disabled = !current;
      }
      state.employeeFormBaseline = employeeComparableSnapshot(current);
      syncEmployeeSalaryModeUi();
    }

    function readEmployeeFormPayload() {
      return {
        create_mode: Boolean(state.employeeCreateMode),
        employee_id: state.employeeCreateMode ? '' : (state.activeEmployeeId || ''),
        name: els.employeeNameInput.value,
        position: els.employeePositionInput.value,
        salary_mode: els.employeeSalaryModeInput.value,
        base_salary: els.employeeBaseSalaryInput.value,
        work_percent: els.employeeWorkPercentInput.value,
        note: els.employeeNoteInput.value,
        is_active: Boolean(els.employeeActiveInput.checked),
        actor_name: state.actor,
        source: 'ui',
      };
    }

    function renderEmployeesList() {
      const employees = Array.isArray(state.employees) ? state.employees : [];
      const visibleEmployees = filteredEmployeesList();
      const summaryMap = payrollSummaryMap();
      syncEmployeesVisibilityFilterUi();
      if (els.employeesListMeta) {
        if (!employees.length) {
          els.employeesListMeta.textContent = 'СПИСОК ПУСТ';
        } else {
          els.employeesListMeta.textContent = 'ПОКАЗАНО ' + visibleEmployees.length + ' / ' + employees.length + ' · ЛИМИТ 15';
        }
      }
      if (!els.employeesList) return;
      if (!employees.length) {
        els.employeesList.innerHTML = '<div class="cashboxes-empty">Нет сотрудников.</div>';
        return;
      }
      if (!visibleEmployees.length) {
        els.employeesList.innerHTML = '<div class="cashboxes-empty">Ничего не найдено.</div>';
        return;
      }
      els.employeesList.innerHTML = visibleEmployees.map((employee) => {
        const isActive = !state.employeeCreateMode && employee.id === state.activeEmployeeId;
        const modeLabel = employeeSalaryModeLabel(employee.salary_mode);
        const comp = [
          employee.base_salary ? ('Оклад ' + employee.base_salary) : '',
          employee.work_percent ? (employee.work_percent + '%') : '',
          modeLabel,
        ].filter(Boolean).join(' · ');
        const summary = summaryMap.get(String(employee.id || ''));
        const summaryLabel = String(summary?.works_count || '0') + ' раб.';
        const summaryValue = String(summary?.total_salary || '0');
        return '<button class="employees-row' + (isActive ? ' is-active' : '') + '" type="button" data-employee-id="' + escapeHtml(employee.id) + '">'
          + '<div class="employees-row__top"><div class="employees-row__title">' + escapeHtml(employee.name) + '</div><div class="employees-row__state">' + (employee.is_active ? 'АКТИВЕН' : 'ВЫКЛ') + '</div></div>'
          + '<div class="employees-row__meta">' + escapeHtml(employee.position || 'Без должности') + '</div>'
          + '<div class="employees-row__comp">' + escapeHtml(comp || modeLabel) + '</div>'
          + '<div class="employees-row__summary"><span>' + escapeHtml(summaryLabel) + '</span><strong>' + escapeHtml(summaryValue) + '</strong></div>'
          + '</button>';
      }).join('');
    }

    function renderEmployeesSummary() {
      const rows = Array.isArray(state.payrollReport?.summary) ? state.payrollReport.summary : [];
      if (!rows.length) {
        els.employeesSummaryTable.innerHTML = '<tr><td colspan="6">Начислений нет.</td></tr>';
        return;
      }
      els.employeesSummaryTable.innerHTML = rows.map((row) => {
        const isActive = String(row.employee_id || '') === String(state.activeEmployeeId || '');
        return '<tr data-employee-id="' + escapeHtml(row.employee_id) + '"' + (isActive ? ' class="is-active"' : '') + '>' +
          '<td>' + escapeHtml(row.employee_name || '-') + '</td>' +
          '<td>' + escapeHtml(row.position || '-') + '</td>' +
          '<td class="is-num">' + escapeHtml(row.works_count || 0) + '</td>' +
          '<td class="is-num">' + escapeHtml(row.accrued_total || '0') + '</td>' +
          '<td class="is-num">' + escapeHtml(row.base_salary || '0') + '</td>' +
          '<td class="is-num">' + escapeHtml(row.total_salary || '0') + '</td>' +
        '</tr>';
      }).join('');
    }

    function renderEmployeesSummaryStrip() {
      const selectedEmployee = selectedEmployeeRecord();
      const rows = Array.isArray(state.payrollReport?.summary) ? state.payrollReport.summary : [];
      const selectedSummary = rows.find((item) => String(item.employee_id || '') === String(state.activeEmployeeId || '')) || null;
      if (!selectedEmployee) {
        els.employeesSummaryStrip.innerHTML = '';
        return;
      }
      const kpis = [
        { label: 'ОКЛАД', value: selectedSummary?.base_salary || selectedEmployee.base_salary || '0' },
        { label: '%', value: selectedEmployee.work_percent || '0' },
        { label: 'РАБОТ', value: selectedSummary?.works_count || '0' },
        { label: 'ИТОГ', value: selectedSummary?.total_salary || '0', accent: true },
      ];
      els.employeesSummaryStrip.innerHTML = kpis.map((item) => (
        '<div class="employees-kpi' + (item.accent ? ' employees-kpi--accent' : '') + '"><div class="employees-kpi__label">' + escapeHtml(item.label) + '</div><div class="employees-kpi__value">' + escapeHtml(item.value) + '</div></div>'
      )).join('');
    }

    function renderEmployeesDetails() {
      const selectedId = String(state.activeEmployeeId || '').trim();
      const rows = Array.isArray(state.payrollReport?.detail_rows) ? state.payrollReport.detail_rows : [];
      const visibleRows = selectedId ? rows.filter((item) => String(item.employee_id || '').trim() === selectedId) : [];
      if (!visibleRows.length) {
        els.employeesDetailTable.innerHTML = '<tr><td colspan="6">' + (selectedId ? 'Строк начисления нет.' : 'Выберите сотрудника.') + '</td></tr>';
        return;
      }
      els.employeesDetailTable.innerHTML = visibleRows.map((row) => {
        return '<tr data-card-id="' + escapeHtml(row.card_id || '') + '" data-open-repair-order="' + (row.repair_order_number ? '1' : '') + '">' +
          '<td>' + escapeHtml(row.closed_at || '-') + '</td>' +
          '<td>' + escapeHtml(row.repair_order_number || '-') + '</td>' +
          '<td>' + escapeHtml(row.vehicle || '-') + '</td>' +
          '<td>' + escapeHtml(row.work_name || '-') + '</td>' +
          '<td class="is-num">' + escapeHtml(row.work_total || '0') + '</td>' +
          '<td class="is-num">' + escapeHtml(row.salary_amount || '0') + '</td>' +
        '</tr>';
      }).join('');
    }

    function renderEmployeesWorkspace() {
      const employees = Array.isArray(state.employees) ? state.employees : [];
      if (!state.employeeCreateMode && !state.activeEmployeeId && employees.length) {
        state.activeEmployeeId = employees[0].id;
      }
      if (state.activeEmployeeId && !employees.some((item) => item.id === state.activeEmployeeId)) {
        state.activeEmployeeId = employees[0]?.id || '';
      }
      if (!employees.length) {
        state.activeEmployeeId = '';
        state.employeeCreateMode = true;
      }
      if (els.employeesMonthInput) {
        els.employeesMonthInput.value = state.payrollMonth || currentPayrollMonthValue();
      }
      if (els.employeesSearchInput) {
        els.employeesSearchInput.value = state.employeesQuery || '';
      }
      fillEmployeeForm(state.employeeCreateMode ? null : selectedEmployeeRecord());
      renderEmployeesList();
      renderEmployeesSummary();
      renderEmployeesSummaryStrip();
      renderEmployeesDetails();
      syncEmployeesReportTabUi();
      const summaryRows = Array.isArray(state.payrollReport?.summary) ? state.payrollReport.summary : [];
      const detailRows = Array.isArray(state.payrollReport?.detail_rows) ? state.payrollReport.detail_rows : [];
      renderEmployeeProfileMeta();
      if (els.employeesReportMeta) {
        els.employeesReportMeta.textContent = 'СОТРУДНИКОВ ' + summaryRows.length + ' · СТРОК ' + detailRows.length;
      }
    }

    async function loadEmployeesReference() {
      const month = state.payrollMonth || currentPayrollMonthValue();
      const data = await api('/api/list_employees?month=' + encodeURIComponent(month));
      state.employees = Array.isArray(data?.employees) ? data.employees : [];
      if (!state.employeeCreateMode && !state.activeEmployeeId && state.employees.length) {
        state.activeEmployeeId = state.employees[0].id;
      }
      if (!state.employees.length) {
        state.employeeCreateMode = true;
      }
      return data;
    }

    async function loadPayrollReport() {
      const month = state.payrollMonth || currentPayrollMonthValue();
      state.payrollReport = await api('/api/get_payroll_report?month=' + encodeURIComponent(month));
      return state.payrollReport;
    }

    function refreshRepairOrderEmployeeSelects() {
      if (!els.repairOrderModal?.classList.contains('is-open')) return;
      renderRepairOrderRows('works', readRepairOrderRows('works'));
    }

    async function loadEmployeesWorkspace(openModal = false) {
      state.payrollMonth = (els.employeesMonthInput?.value || state.payrollMonth || currentPayrollMonthValue());
      await loadEmployeesReference();
      await loadPayrollReport();
      renderEmployeesWorkspace();
      refreshRepairOrderEmployeeSelects();
      if (openModal) els.employeesModal.classList.add('is-open');
    }

    function resetEmployeeForm() {
      if (!confirmDiscardEmployeeChanges()) return;
      state.employeesQuery = '';
      state.activeEmployeeId = '';
      state.employeeCreateMode = true;
      if (els.employeesSearchInput) els.employeesSearchInput.value = '';
      fillEmployeeForm(null);
      renderEmployeesList();
      renderEmployeesSummaryStrip();
      renderEmployeesDetails();
      renderEmployeeProfileMeta();
      requestAnimationFrame(() => els.employeeNameInput?.focus());
      setStatus('РЕЖИМ СОЗДАНИЯ СОТРУДНИКА.', false);
    }

    function openEmployeesModal() {
      ensureEmployeesUi();
      hydrateEmployeesUiRefs();
      bindEmployeesUiEvents();
      if (els.employeesMonthInput && !els.employeesMonthInput.value) {
        els.employeesMonthInput.value = state.payrollMonth || currentPayrollMonthValue();
      }
      loadEmployeesWorkspace(true).catch((error) => setStatus(error.message, true));
    }

    async function saveEmployee() {
      const employeeName = String(els.employeeNameInput?.value || '').trim();
      if (!employeeName) {
        if (els.employeeNameInput) els.employeeNameInput.focus();
        setStatus('УКАЖИ ИМЯ СОТРУДНИКА.', true);
        return;
      }
      try {
        const data = await api('/api/save_employee', { method: 'POST', body: readEmployeeFormPayload() });
        state.employees = Array.isArray(data?.employees) ? data.employees : [];
        state.employeeCreateMode = false;
        state.activeEmployeeId = data?.employee?.id || state.activeEmployeeId;
        state.employeesQuery = '';
        if (els.employeesSearchInput) els.employeesSearchInput.value = '';
        if (data?.employee && !data.employee.is_active) {
          state.employeesVisibilityFilter = 'all';
        }
        await loadPayrollReport();
        renderEmployeesWorkspace();
        refreshRepairOrderEmployeeSelects();
        setStatus(data?.created ? 'СОТРУДНИК ДОБАВЛЕН.' : 'СОТРУДНИК СОХРАНЕН.', false);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function deleteEmployee() {
      const employee = selectedEmployeeRecord();
      if (!employee) {
        setStatus('ВЫБЕРИ СОТРУДНИКА ДЛЯ УДАЛЕНИЯ.', true);
        return;
      }
      if (!window.confirm('Удалить сотрудника "' + String(employee.name || 'Сотрудник') + '"?')) return;
      try {
        const data = await api('/api/delete_employee', {
          method: 'POST',
          body: {
            employee_id: employee.id,
            actor_name: state.actor,
            source: 'ui',
          },
        });
        state.employees = Array.isArray(data?.employees) ? data.employees : [];
        if (String(state.activeEmployeeId || '') === String(employee.id || '')) {
          state.activeEmployeeId = '';
        }
        state.employeeCreateMode = !state.employees.length;
        await loadPayrollReport();
        renderEmployeesWorkspace();
        refreshRepairOrderEmployeeSelects();
        setStatus('СОТРУДНИК УДАЛЕН.', false);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    function handleEmployeesListClick(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const row = target.closest('[data-employee-id]');
      if (!(row instanceof HTMLElement)) return;
      const nextEmployeeId = String(row.dataset.employeeId || '').trim();
      if (!nextEmployeeId || nextEmployeeId === state.activeEmployeeId) return;
      if (!confirmDiscardEmployeeChanges()) return;
      state.employeeCreateMode = false;
      state.activeEmployeeId = nextEmployeeId;
      renderEmployeesWorkspace();
    }

    function handleEmployeesSearchInput(event) {
      const target = event.target;
      if (!(target instanceof HTMLInputElement)) return;
      state.employeesQuery = target.value || '';
      renderEmployeesList();
    }

    function handleEmployeesVisibilityFilterClick(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const button = target.closest('[data-filter]');
      if (!(button instanceof HTMLElement)) return;
      state.employeesVisibilityFilter = normalizeEmployeesVisibilityFilter(button.dataset.filter);
      renderEmployeesList();
    }

    function handleEmployeesReportTabClick(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const button = target.closest('[data-employees-report-tab]');
      if (!(button instanceof HTMLElement)) return;
      state.employeesReportTab = normalizeEmployeesReportTab(button.dataset.employeesReportTab);
      syncEmployeesReportTabUi();
    }

    function handleEmployeesModalFormInput(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      if (target === els.employeeSalaryModeInput) {
        syncEmployeeSalaryModeUi();
        return;
      }
      if (
        target === els.employeeNameInput
        || target === els.employeePositionInput
        || target === els.employeeBaseSalaryInput
        || target === els.employeeWorkPercentInput
        || target === els.employeeNoteInput
        || target === els.employeeActiveInput
      ) {
        if (els.employeeNoteDetails && target === els.employeeNoteInput && String(els.employeeNoteInput.value || '').trim()) {
          els.employeeNoteDetails.open = true;
        }
        renderEmployeeProfileMeta();
      }
    }

    async function handleEmployeesDetailClick(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const row = target.closest('[data-card-id]');
      if (!(row instanceof HTMLElement)) return;
      const cardId = String(row.dataset.cardId || '').trim();
      if (!cardId) return;
      if (!confirmDiscardEmployeeChanges()) return;
      try {
        els.employeesModal.classList.remove('is-open');
        await openCardById(cardId);
        if (String(row.dataset.openRepairOrder || '') === '1') {
          await openRepairOrderModal();
        }
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    function boardAgentContext() {
      const columns = Array.isArray(state.snapshot?.columns) ? state.snapshot.columns : [];
      const cards = Array.isArray(state.snapshot?.cards) ? state.snapshot.cards : [];
      return {
        kind: 'board',
        revision: String(state.snapshot?.meta?.revision || ''),
        active_cards: cards.length,
        archived_cards: archivedCardsTotal(),
        columns: columns.map((column) => ({ id: column.id, label: column.label })),
      };
    }

    function cardAgentContext() {
      const payload = currentCardPayload();
      const vehicleProfile = readVehicleProfileForm();
      const cardId = String(state.editingId || state.activeCard?.id || '').trim();
      const repairOrder = els.repairOrderModal?.classList.contains('is-open')
        ? readRepairOrderFromForm()
        : repairOrderCardDraft(state.activeCard, state.activeCard?.repair_order || {});
      return {
        kind: 'card',
        card_id: cardId,
        title: payload.title,
        vehicle: payload.vehicle,
        description: payload.description,
        column: payload.column,
        tags: payload.tags,
        vin: String(vehicleProfile?.vin || repairOrder?.vin || '').trim(),
        vehicle_profile: vehicleProfile,
        repair_order: repairOrder,
      };
    }

    function buildAgentContext(kind) {
      return String(kind || '').trim().toLowerCase() === 'card' ? cardAgentContext() : boardAgentContext();
    }

    const AI_SURFACE_SCENARIO_IDS = ['ai_chat', 'full_card_enrichment', 'board_control'];
    const AI_SURFACE_ENTRY_IDS = {
      ai_chat: 'future_ai_chat_window',
      full_card_enrichment: 'future_card_enrichment_trigger',
      board_control: 'future_board_control_toggle',
    };

    function buildAiSurfaceContext(kind) {
      const normalizedKind = String(kind || '').trim().toLowerCase();
      if (normalizedKind === 'card') {
        const context = cardAgentContext();
        return {
          ...context,
          kind: 'card',
          surface: 'ai_entry',
          scenario_id: 'full_card_enrichment',
          scenario_context: buildAiFullCardEnrichmentContextPacket(),
        };
      }
      if (normalizedKind === 'board') {
        const context = boardAgentContext();
        return {
          ...context,
          kind: 'board',
          surface: 'ai_entry',
          scenario_id: 'board_control',
          scenario_context: buildAiScenarioContextPacket('board_control'),
        };
      }
      const context = buildAiChatWindowContext();
      return {
        ...context,
        kind: 'chat',
        surface: 'ai_entry',
        scenario_id: 'ai_chat',
        scenario_context: context,
      };
    }

    function activeAiTaskContext() {
      if (els.aiSurfaceModal?.classList.contains('is-open') && state.aiSurfaceContext && typeof state.aiSurfaceContext === 'object') {
        return state.aiSurfaceContext;
      }
      if (els.agentModal?.classList.contains('is-open') && state.agentContext && typeof state.agentContext === 'object') {
        return state.agentContext;
      }
      if (els.aiChatWindow?.classList.contains('is-open') && state.aiChatWindowContext && typeof state.aiChatWindowContext === 'object') {
        return state.aiChatWindowContext;
      }
      if (state.aiSurfaceContext && typeof state.aiSurfaceContext === 'object') {
        return state.aiSurfaceContext;
      }
      if (state.agentContext && typeof state.agentContext === 'object') {
        return state.agentContext;
      }
      if (state.aiChatWindowContext && typeof state.aiChatWindowContext === 'object') {
        return state.aiChatWindowContext;
      }
      return { kind: 'board' };
    }

    function formatAiSurfaceContextLabel(context) {
      const normalized = context && typeof context === 'object' ? context : { kind: 'chat' };
      if (String(normalized.kind || '').trim().toLowerCase() === 'card') {
        const heading = String(normalized.title || normalized.vehicle || normalized.card_id || 'карточка').trim();
        return 'РЕЖИМ: КАРТОЧКА · ' + heading;
      }
      if (String(normalized.kind || '').trim().toLowerCase() === 'board') {
        return 'РЕЖИМ: ДОСКА';
      }
      return 'РЕЖИМ: ОБЩИЙ ЧАТ';
    }

    function buildFullCardEnrichmentRequestPayload() {
      const card = state.activeCard && typeof state.activeCard === 'object' ? state.activeCard : null;
      const cardId = String(card?.id || state.editingId || '').trim();
      return {
        card_id: cardId,
        actor_name: state.actor,
        scenario_id: 'full_card_enrichment',
        prompt: String(card?.ai_autofill_prompt || '').trim(),
        context_packet: buildAiFullCardEnrichmentContextPacket(),
      };
    }

    function aiSurfaceScenarioEntryId(scenarioId) {
      return AI_SURFACE_ENTRY_IDS[String(scenarioId || '').trim().toLowerCase()] || '';
    }

    function aiSurfaceExposureTone(exposureState) {
      const normalized = String(exposureState || '').trim().toLowerCase();
      if (normalized === 'primary' || normalized === 'available') return 'online';
      if (normalized === 'gated' || normalized === 'legacy_only') return 'busy';
      if (normalized === 'replaced') return 'idle';
      if (normalized === 'hidden') return 'idle';
      return 'idle';
    }

    function aiSurfaceExposureLabel(exposureState) {
      const normalized = String(exposureState || '').trim().toLowerCase();
      if (normalized === 'primary') return 'ОСНОВНОЙ';
      if (normalized === 'available') return 'ДОСТУПНО';
      if (normalized === 'gated') return 'ОГРАНИЧЕНО';
      if (normalized === 'legacy_only') return 'РЕЗЕРВ';
      if (normalized === 'replaced') return 'ЗАМЕНЕНО';
      return 'СКРЫТО';
    }

    function aiSurfaceScenarioMeta(scenario) {
      const parts = [];
      const trigger = String(scenario?.trigger_kind || '').trim();
      const scope = String(scenario?.scope_kind || '').trim();
      const writePolicy = String(scenario?.write_policy || '').trim();
      if (trigger) parts.push(trigger);
      if (scope) parts.push(scope);
      if (writePolicy) parts.push(writePolicy);
      return parts.join(' · ');
    }

    function aiSurfaceScenarioTitle(scenarioId, scenario) {
      const normalized = String(scenarioId || '').trim().toLowerCase();
      if (normalized === 'ai_chat') return 'Чат с AI';
      if (normalized === 'full_card_enrichment') return 'Обогатить карточку';
      if (normalized === 'board_control') return 'Фоновый контроль доски';
      return String(scenario?.display_intent || scenarioId || 'AI').trim();
    }

    function aiSurfaceScenarioDescription(scenarioId) {
      const normalized = String(scenarioId || '').trim().toLowerCase();
      if (normalized === 'ai_chat') {
        return 'Открыть отдельное окно чата для вопросов, разбора ситуации и работы с контекстом CRM.';
      }
      if (normalized === 'full_card_enrichment') {
        return 'Аккуратно дополнить открытую карточку, использовать найденные факты и проверить запись после изменений.';
      }
      if (normalized === 'board_control') {
        return 'Фоновый режим для тихой поддержки качества карточек. Работает по правилам и без ручного чата.';
      }
      return 'Отдельный AI-сценарий.';
    }

    function aiSurfaceScenarioUserMeta(scenarioId) {
      const normalized = String(scenarioId || '').trim().toLowerCase();
      if (normalized === 'ai_chat') return 'Ручной режим · без автоматических изменений';
      if (normalized === 'full_card_enrichment') return 'Только для открытой карточки · безопасные изменения';
      if (normalized === 'board_control') return 'Фоновый режим · работает по расписанию';
      return '';
    }

    function aiSurfacePrimaryPathSummary(primaryPath) {
      const normalized = String(primaryPath || '').trim().toLowerCase();
      if (normalized === 'ai_chat') return 'Сейчас главным ручным входом считается AI-чат.';
      if (normalized === 'full_card_enrichment') return 'Сейчас главным ручным входом считается AI-обогащение карточки.';
      return 'Новый AI-интерфейс уже отделён от старого меню агента.';
    }

    function aiSurfaceAvailableScenarioSummary(availableScenarioIds) {
      const scenarioIds = Array.isArray(availableScenarioIds) ? availableScenarioIds : [];
      const titles = scenarioIds
        .map((scenarioId) => aiSurfaceScenarioTitle(scenarioId))
        .filter(Boolean);
      if (!titles.length) return 'Сейчас доступных AI-сценариев не отмечено.';
      return 'Сценарии в этом режиме: ' + titles.join(', ') + '.';
    }

    function aiSurfaceScenarioSourceList(scenario) {
      const sources = Array.isArray(scenario?.context_sources) ? scenario.context_sources : [];
      return sources.map((item) => String(item || '').trim()).filter(Boolean).join(' · ');
    }

    function aiSurfaceScenarioBoundaryList(scenario) {
      const boundaries = Array.isArray(scenario?.boundaries) ? scenario.boundaries : [];
      return boundaries.map((item) => String(item || '').trim()).filter(Boolean).join(' · ');
    }

    function aiSurfaceScenarioNonGoalList(scenario) {
      const nonGoals = Array.isArray(scenario?.non_goals) ? scenario.non_goals : [];
      return nonGoals.map((item) => String(item || '').trim()).filter(Boolean).join(' · ');
    }

    function aiSurfaceLegacyFallbackVisible(statusPayload, selectedExposureState) {
      const payload = statusPayload && typeof statusPayload === 'object' ? statusPayload : {};
      const aiRemodel = payload.ai_remodel && typeof payload.ai_remodel === 'object' ? payload.ai_remodel : {};
      const effectiveMode = aiRemodel.effective_mode && typeof aiRemodel.effective_mode === 'object' ? aiRemodel.effective_mode : {};
      const entryExposureMap = effectiveMode.entry_exposure && typeof effectiveMode.entry_exposure === 'object' ? effectiveMode.entry_exposure : {};
      const selectedState = String(selectedExposureState || '').trim().toLowerCase();
      const legacyEnabled = Boolean(aiRemodel.legacy_ux_enabled ?? effectiveMode.legacy_ux_enabled ?? true);
      const legacyPromptState = String(entryExposureMap.agent_manual_prompt?.exposure_state || entryExposureMap.quick_prompts?.exposure_state || 'hidden').trim().toLowerCase();
      return legacyEnabled && legacyPromptState === 'legacy_only' && selectedState === 'legacy_only';
    }

    function isFullCardEnrichmentTask(task) {
      const metadata = task?.metadata && typeof task.metadata === 'object' ? task.metadata : {};
      const scenarioId = String(metadata.scenario_id || '').trim().toLowerCase();
      const source = String(task?.source || '').trim().toLowerCase();
      const trigger = String(metadata.trigger || '').trim().toLowerCase();
      return scenarioId === 'full_card_enrichment'
        || source === 'ui_full_card_enrichment'
        || trigger === 'manual_enrichment';
    }

    function currentCardEnrichmentTask(tasks, options = {}) {
      const items = Array.isArray(tasks) ? tasks : [];
      const includeTerminal = Boolean(options.includeTerminal);
      const card = currentAgentContextCard();
      const cardId = String(card?.id || '').trim();
      if (!cardId) return null;
      return items.find((item) => {
        const metadata = item?.metadata && typeof item.metadata === 'object' ? item.metadata : {};
        const context = metadata.context && typeof metadata.context === 'object' ? metadata.context : {};
        const status = String(item?.status || '').trim().toLowerCase();
        if (String(metadata.purpose || '').trim().toLowerCase() !== 'card_autofill') return false;
        if (String(context.card_id || '').trim() !== cardId) return false;
        if (!isFullCardEnrichmentTask(item)) return false;
        if (includeTerminal) return true;
        return status === 'pending' || status === 'running';
      }) || null;
    }

    function applyAiSurfaceEntryState(button, exposureRecord, options = {}) {
      if (!(button instanceof HTMLElement)) return;
      const keepVisibleWhenHidden = Boolean(options.keepVisibleWhenHidden);
      const label = String(options.label || 'AI').trim() || 'AI';
      const exposureState = String(exposureRecord?.exposure_state || 'hidden').trim().toLowerCase();
      const tone = aiSurfaceExposureTone(exposureState);
      const exposureLabel = aiSurfaceExposureLabel(exposureState);
      const hidden = exposureState === 'hidden' || exposureState === 'replaced';
      button.dataset.state = tone;
      button.dataset.exposure = exposureState;
      button.title = label + ' · ' + exposureLabel;
      button.setAttribute('aria-label', button.title);
      button.hidden = hidden && !keepVisibleWhenHidden;
      if (button.hidden) {
        button.dataset.visible = 'false';
      } else {
        button.dataset.visible = 'true';
      }
    }

    function renderFullCardEnrichmentEntryState(statusPayload) {
      if (!(els.cardAgentButton instanceof HTMLElement)) return;
      const payload = statusPayload && typeof statusPayload === 'object' ? statusPayload : {};
      const effectiveMode = payload.ai_remodel?.effective_mode && typeof payload.ai_remodel.effective_mode === 'object'
        ? payload.ai_remodel.effective_mode
        : {};
      const entryExposure = effectiveMode.entry_exposure?.future_card_enrichment_trigger && typeof effectiveMode.entry_exposure.future_card_enrichment_trigger === 'object'
        ? effectiveMode.entry_exposure.future_card_enrichment_trigger
        : {};
      const exposureState = String(entryExposure.exposure_state || '').trim().toLowerCase();
      const latestTask = currentCardEnrichmentTask(state.agentLatestTasks, { includeTerminal: true });
      const latestStatus = String(latestTask?.status || '').trim().toLowerCase();
      const agentReady = Boolean(payload.agent?.ready ?? payload.agent?.available ?? payload.agent?.enabled);
      let uiState = 'idle';
      let title = 'AI карточки';
      if (latestStatus === 'pending' || latestStatus === 'running') {
        uiState = 'busy';
        title = 'AI карточки · идёт обогащение';
      } else if (latestStatus === 'failed') {
        uiState = 'error';
        title = 'AI карточки · ошибка обогащения';
      } else if (agentReady && exposureState !== 'hidden' && exposureState !== 'replaced' && currentAgentContextCard()?.id) {
        uiState = 'online';
        title = 'AI карточки · готово к обогащению';
      }
      els.cardAgentButton.dataset.state = uiState;
      els.cardAgentButton.title = title;
      els.cardAgentButton.setAttribute('aria-label', title);
    }

    function renderFullCardEnrichmentResult(statusPayload, scenario, selectedExposureLabel) {
      const payload = statusPayload && typeof statusPayload === 'object' ? statusPayload : {};
      const card = currentAgentContextCard();
      const cardId = String(card?.id || '').trim();
      const contextPacket = buildAiFullCardEnrichmentContextPacket();
      const task = currentCardEnrichmentTask(state.agentLatestTasks, { includeTerminal: true });
      const status = String(task?.status || '').trim().toLowerCase();
      if (!cardId) {
        return {
          state: 'empty',
          tone: '',
          html: '<div class="agent-result__fallback">Открой карточку, чтобы запустить AI-обогащение.</div>',
        };
      }
      if (status === 'pending' || status === 'running') {
        const summaryParts = [
          contextPacket.card_context?.summary_label || contextPacket.card_label || 'Карточка',
          contextPacket.repair_order_context?.summary_label || contextPacket.repair_order_label || '',
          contextPacket.wall_digest?.label || '',
          contextPacket.attachments_intake?.label || '',
        ].filter(Boolean);
        return {
          state: 'active',
          tone: 'warning',
          html: '<div class="agent-result__fallback"><strong>AI-обогащение выполняется.</strong><br>'
            + escapeHtml(summaryParts.join(' · ') || 'Идёт bounded pipeline full_card_enrichment.')
            + (task?.id ? '<br><br>Task: ' + escapeHtml(String(task.id || '')) : '')
            + '</div>',
        };
      }
      if (status === 'failed') {
        return {
          state: 'error',
          tone: 'error',
          html: '<div class="agent-result__fallback"><strong>AI-обогащение завершилось с ошибкой.</strong><br>'
            + escapeHtml(formatAgentErrorMessage(task?.error || 'Не удалось выполнить bounded enrichment.'))
            + '</div>',
        };
      }
      if (status === 'completed') {
        const display = normalizeAgentDisplay(task);
        const auditBits = [
          selectedExposureLabel ? 'СТАТУС: ' + selectedExposureLabel : '',
          contextPacket.card_context?.summary_label ? 'КАРТОЧКА: ' + contextPacket.card_context.summary_label : '',
          contextPacket.repair_order_context?.summary_label ? 'ЗН: ' + contextPacket.repair_order_context.summary_label : '',
          contextPacket.wall_digest?.label ? 'СТЕНА: ' + contextPacket.wall_digest.label : '',
        ].filter(Boolean);
        return {
          state: 'filled',
          tone: display.tone || 'success',
          html: (display.title || display.summary || display.sections.length || display.actions.length)
            ? renderAgentDisplay(display) + (auditBits.length ? '<div class="agent-result__fallback">' + escapeHtml(auditBits.join(' · ')) + '</div>' : '')
            : '<div class="agent-result__fallback">' + escapeHtml(String(task?.summary || task?.result || 'AI-обогащение завершено.').trim()) + '</div>',
        };
      }
      const sourceList = Array.isArray(scenario?.context_sources) ? scenario.context_sources.map((item) => String(item || '').trim()).filter(Boolean).join(' · ') : '';
      const readyParts = [
        contextPacket.card_context?.summary_label || contextPacket.card_label || 'Карточка',
        contextPacket.repair_order_context?.summary_label || contextPacket.repair_order_label || '',
        contextPacket.wall_digest?.label || '',
        contextPacket.attachments_intake?.label || '',
      ].filter(Boolean);
      return {
        state: 'empty',
        tone: '',
        html: '<div class="agent-result__fallback"><strong>Full Card Enrichment</strong><br>'
          + escapeHtml(readyParts.join(' · ') || 'Контекст карточки готов.')
          + '<br><br>Источники: ' + escapeHtml(sourceList || 'card_context · repair_order_context · wall_digest · attachments')
          + '<br>Trigger: card-scoped bounded pipeline with verify.'
          + '</div>',
      };
    }

    function renderAiChatWindow(statusPayload) {
      const payload = statusPayload && typeof statusPayload === 'object' ? statusPayload : {};
      const aiRemodel = payload.ai_remodel && typeof payload.ai_remodel === 'object' ? payload.ai_remodel : {};
      const effectiveMode = aiRemodel.effective_mode && typeof aiRemodel.effective_mode === 'object' ? aiRemodel.effective_mode : {};
      const modeConfig = effectiveMode.mode_config && typeof effectiveMode.mode_config === 'object' ? effectiveMode.mode_config : {};
      const scenarioStateMap = modeConfig.scenario_state && typeof modeConfig.scenario_state === 'object' ? modeConfig.scenario_state : {};
      const entryExposureMap = effectiveMode.entry_exposure && typeof effectiveMode.entry_exposure === 'object' ? effectiveMode.entry_exposure : {};
      const scenarioState = scenarioStateMap.ai_chat && typeof scenarioStateMap.ai_chat === 'object' ? scenarioStateMap.ai_chat : {};
      const entryExposure = entryExposureMap.future_ai_chat_window && typeof entryExposureMap.future_ai_chat_window === 'object'
        ? entryExposureMap.future_ai_chat_window
        : null;
      const exposureState = String(
        entryExposure?.exposure_state
        || scenarioState.rollout_state
        || 'hidden'
      ).trim().toLowerCase();
      const tone = aiSurfaceExposureTone(exposureState);
      const label = aiSurfaceExposureLabel(exposureState);
      state.aiChatWindowStatusPayload = payload;
      refreshAiCompactContextPacket();
      if (els.aiChatWindowTitle) {
        els.aiChatWindowTitle.textContent = 'AI / ЧАТ';
      }
      if (els.aiChatWindowSubtitle) {
        els.aiChatWindowSubtitle.textContent = 'Отдельный рабочий AI surface для будущего чата. Layout уже готов для длинных ответов, истории и настроек.';
      }
      if (els.aiChatWindowStatusLabel) {
        els.aiChatWindowStatusLabel.textContent = label;
        els.aiChatWindowStatusLabel.dataset.state = tone;
      }
      renderAiChatWindowContext();
      renderAiChatWindowSettings();
      if (els.aiChatWindowMessages) {
        els.aiChatWindowMessages.dataset.state = exposureState;
      }
      if (els.aiChatWindowInput) {
        els.aiChatWindowInput.placeholder = exposureState === 'hidden'
          ? 'AI-чат пока скрыт в текущем rollout'
          : 'Напиши сообщение для AI-чата...';
      }
      if (els.aiChatWindowSendButton) {
        els.aiChatWindowSendButton.disabled = false;
      }
      state.aiChatWindowHistoryContext = aiChatHistoryContextSnapshot();
      renderAiChatWindowHistory();
    }

    function openAiChatWindow() {
      if (!requireOperatorSession()) return;
      hydrateAiChatWindowUiRefs();
      bindAiChatWindowUiEvents();
      closeAiSurface();
      closeAgentModal();
      refreshAiCompactContextPacket();
      state.aiChatWindowContext = buildAiChatWindowContext();
      state.aiSurfaceContext = state.aiChatWindowContext;
      state.aiSurfaceSelectedScenario = 'ai_chat';
      state.aiChatWindowSettingsOpen = false;
      state.aiChatWindowHistoryContext = aiChatHistoryContextSnapshot();
      ensureAiChatWindowHistory();
      renderAiChatWindow(state.aiSurfaceStatusPayload || state.agentStatusPayload || {});
      els.aiChatWindow?.classList.add('is-open');
      focusAiChatWindowInput();
      refreshAgentModalState();
    }

    function closeAiChatWindow() {
      els.aiChatWindow?.classList.remove('is-open');
      if (!els.agentModal?.classList.contains('is-open') && !els.aiSurfaceModal?.classList.contains('is-open')) {
        if (state.agentRefreshTimer) {
          window.clearTimeout(state.agentRefreshTimer);
          state.agentRefreshTimer = null;
        }
      }
    }

    function formatAgentContextLabel(context) {
      const normalized = context && typeof context === 'object' ? context : { kind: 'board' };
      if (String(normalized.kind || '').trim().toLowerCase() === 'card') {
        const heading = String(normalized.title || normalized.vehicle || normalized.card_id || 'карточка').trim();
        return 'КОНТЕКСТ: КАРТОЧКА · ' + heading;
      }
      return 'КОНТЕКСТ: ДОСКА';
    }

    function agentPlaceholder(context) {
      if (String(context?.kind || '').trim().toLowerCase() === 'card') {
        return 'Расшифруй VIN этой карточки';
      }
      return 'Сделай обзор доски';
    }

    function quickAgentPrompts(context) {
      if (String(context?.kind || '').trim().toLowerCase() === 'card') {
        return [
          { label: 'VIN', template: 'vin', prompt: 'Расшифруй VIN этой карточки, используй внешний VIN-декодер и сразу примени в карточку только подтверждённое заполнение паспорта автомобиля.' },
          { label: 'ЗАПЧАСТИ', template: 'parts', prompt: 'Процени запчасти на этот автомобиль и подбери каталожные номера.' },
          { label: 'ТО', template: 'maintenance', prompt: 'Процени ТО на этот автомобиль.' },
          { label: 'ПОРЯДОК', template: 'cleanup', prompt: 'Наведи порядок в этой карточке: структурируй описание без потери информации, автоматически заполни недостающие поля по возможности и сразу примени уверенные изменения в карточку для краткой сути, тегов и паспорта автомобиля. Ничего не выдумывай, а в ответе кратко перечисли, что изменено.' },
          { label: 'КАРТОЧКА', template: 'card_fill', prompt: 'Заполни карточку по описанию и предложи структуру данных.' },
        ];
      }
      return [
        { label: 'ОБЗОР', prompt: 'Сделай обзор доски и покажи приоритетные проблемы.' },
        { label: 'ПРОСРОЧКИ', prompt: 'Найди просроченные карточки и коротко перечисли их.' },
        { label: 'ОПЛАТЫ', prompt: 'Проверь неоплаченные заказ-наряды и покажи краткую сводку.' },
        { label: 'КАССЫ', prompt: 'Покажи краткую сводку по кассам и последним движениям.' },
      ];
    }

    function handleAiSurfaceScenarioClick(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const button = target.closest('[data-scenario]');
      if (!(button instanceof HTMLElement)) return;
      const scenarioId = String(button.dataset.scenario || '').trim().toLowerCase();
      if (!AI_SURFACE_SCENARIO_IDS.includes(scenarioId)) return;
      state.aiSurfaceSelectedScenario = scenarioId;
      renderAiEntrySurface(state.aiSurfaceStatusPayload || {});
    }

    function handleAiSurfaceLegacyClick() {
      if (!aiSurfaceLegacyFallbackVisible(state.aiSurfaceStatusPayload || {}, state.aiSurfaceSelectedScenario)) return;
      closeAiSurface();
      openAgentModal(String(state.aiSurfaceContext?.kind || '').trim().toLowerCase() === 'card' ? 'card' : 'board');
    }

    function openAiChatEntry() {
      openAiChatWindow();
    }

    async function runFullCardEnrichment() {
      if (!requireOperatorSession()) return;
      const payload = buildFullCardEnrichmentRequestPayload();
      if (!String(payload.card_id || '').trim()) {
        return setStatus('ОТКРОЙ КАРТОЧКУ ДЛЯ AI-ОБОГАЩЕНИЯ.', true);
      }
      hydrateAiSurfaceUiRefs();
      bindAiSurfaceUiEvents();
      closeAiChatWindow();
      closeAgentModal();
      state.aiSurfaceContext = buildAiSurfaceContext('card');
      state.aiSurfaceSelectedScenario = 'full_card_enrichment';
      els.aiSurfaceModal?.classList.add('is-open');
      renderAiEntrySurface(state.aiSurfaceStatusPayload || state.agentStatusPayload || {});
      const activeTask = currentCardEnrichmentTask(state.agentLatestTasks);
      if (activeTask) {
        state.agentTaskId = String(activeTask.id || '').trim();
        setStatus('AI-обогащение уже выполняется.', true);
        await refreshAgentModalState();
        return;
      }
      try {
        const data = await api('/api/run_full_card_enrichment', {
          method: 'POST',
          body: payload,
        });
        if (data?.card) {
          state.activeCard = data.card;
          if (els.cardModal?.classList.contains('is-open')) applyCardModalState(data.card);
        }
        const taskId = String(data?.meta?.task_id || '').trim();
        if (taskId) {
          state.agentTaskId = taskId;
          state.agentLatestTasks = [
            {
              id: taskId,
              source: 'ui_full_card_enrichment',
              status: data?.meta?.already_running ? 'running' : 'pending',
              metadata: {
                purpose: 'card_autofill',
                scenario_id: 'full_card_enrichment',
                trigger: 'manual_enrichment',
                context: { kind: 'card', card_id: String(payload.card_id || '').trim() },
              },
            },
            ...((Array.isArray(state.agentLatestTasks) ? state.agentLatestTasks : []).filter((item) => String(item?.id || '').trim() !== taskId)),
          ];
        }
        renderAiEntrySurface(state.aiSurfaceStatusPayload || state.agentStatusPayload || {});
        setStatus(
          data?.meta?.already_running
            ? 'AI-обогащение уже выполняется.'
            : (data?.meta?.launched ? 'AI-обогащение запущено.' : 'AI-обогащение не запущено.'),
          Boolean(data?.meta?.already_running || !data?.meta?.server_available),
        );
        await refreshAgentModalState();
      } catch (error) {
        renderAiEntrySurface(state.aiSurfaceStatusPayload || state.agentStatusPayload || {});
        setStatus(error.message, true);
      }
    }

    function handleAiSurfaceModalOverlayClick(event) {
      return;
    }

    function bindAiSurfaceUiEvents() {
      if (state.aiSurfaceUiBound) return;
      hydrateAiSurfaceUiRefs();
      els.aiSurfaceScenarioGrid?.addEventListener('click', handleAiSurfaceScenarioClick);
      els.aiSurfaceLegacyButton?.addEventListener('click', handleAiSurfaceLegacyClick);
      els.aiSurfaceModal?.addEventListener('click', handleAiSurfaceModalOverlayClick);
      state.aiSurfaceUiBound = true;
    }

    function renderAiEntrySurface(statusPayload) {
      const payload = statusPayload && typeof statusPayload === 'object' ? statusPayload : {};
      const aiRemodel = payload.ai_remodel && typeof payload.ai_remodel === 'object' ? payload.ai_remodel : {};
      const effectiveMode = aiRemodel.effective_mode && typeof aiRemodel.effective_mode === 'object' ? aiRemodel.effective_mode : {};
      const modeConfig = effectiveMode.mode_config && typeof effectiveMode.mode_config === 'object' ? effectiveMode.mode_config : {};
      const scenarioRegistry = aiRemodel.scenario_registry && typeof aiRemodel.scenario_registry === 'object' ? aiRemodel.scenario_registry : {};
      const scenarioStateMap = modeConfig.scenario_state && typeof modeConfig.scenario_state === 'object' ? modeConfig.scenario_state : {};
      const entryExposureMap = effectiveMode.entry_exposure && typeof effectiveMode.entry_exposure === 'object' ? effectiveMode.entry_exposure : {};
      const selectedScenario = AI_SURFACE_SCENARIO_IDS.includes(state.aiSurfaceSelectedScenario)
        ? state.aiSurfaceSelectedScenario
        : 'ai_chat';
      const selectedEntryId = aiSurfaceScenarioEntryId(selectedScenario);
      const selectedScenarioRegistry = scenarioRegistry[selectedScenario] && typeof scenarioRegistry[selectedScenario] === 'object'
        ? scenarioRegistry[selectedScenario]
        : {};
      const selectedModeState = scenarioStateMap[selectedScenario] && typeof scenarioStateMap[selectedScenario] === 'object'
        ? scenarioStateMap[selectedScenario]
        : {};
      const selectedExposure = selectedEntryId && entryExposureMap[selectedEntryId] && typeof entryExposureMap[selectedEntryId] === 'object'
        ? entryExposureMap[selectedEntryId]
        : null;
      const selectedExposureState = String(
        selectedExposure?.exposure_state
        || selectedModeState.rollout_state
        || selectedScenarioRegistry.rollout_state
        || 'hidden'
      ).trim().toLowerCase();
      const selectedExposureLabel = aiSurfaceExposureLabel(selectedExposureState);
      const selectedExposureTone = aiSurfaceExposureTone(selectedExposureState);
      const legacyEnabled = Boolean(aiRemodel.legacy_ux_enabled ?? effectiveMode.legacy_ux_enabled ?? true);
      state.aiSurfaceStatusPayload = payload;
      if (els.aiSurfaceContextLabel) {
        els.aiSurfaceContextLabel.textContent = formatAiSurfaceContextLabel(state.aiSurfaceContext);
      }
      if (els.aiSurfaceStatusLabel) {
        els.aiSurfaceStatusLabel.textContent = selectedExposureLabel;
        els.aiSurfaceStatusLabel.dataset.state = selectedExposureTone;
      }
      if (els.aiSurfaceSummary) {
        const primaryPath = String(effectiveMode.primary_interactive_path || 'legacy_agent_modal_manual_tasks').trim();
        const available = Array.isArray(effectiveMode.available_scenarios) ? effectiveMode.available_scenarios : [];
        const legacyFallbackVisible = aiSurfaceLegacyFallbackVisible(payload, selectedExposureState);
        els.aiSurfaceSummary.textContent = [
          aiSurfacePrimaryPathSummary(primaryPath),
          legacyFallbackVisible
            ? 'Старое меню доступно только как резервный путь.'
            : 'Старое меню скрыто по умолчанию.',
          aiSurfaceAvailableScenarioSummary(available),
        ].join(' ');
      }
      applyAiSurfaceEntryState(els.aiChatButton, entryExposureMap.future_ai_chat_window, { label: 'AI чат', keepVisibleWhenHidden: true });
      applyAiSurfaceEntryState(els.cardAgentButton, entryExposureMap.future_card_enrichment_trigger, { label: 'AI карточки', keepVisibleWhenHidden: true });
      applyAiSurfaceEntryState(els.agentDockButton, entryExposureMap.future_board_control_toggle, { label: 'AI вход', keepVisibleWhenHidden: false });
      renderFullCardEnrichmentEntryState(payload);
      if (els.aiSurfaceLegacyButton) {
        els.aiSurfaceLegacyButton.hidden = !aiSurfaceLegacyFallbackVisible(payload, selectedExposureState);
        els.aiSurfaceLegacyButton.dataset.state = els.aiSurfaceLegacyButton.hidden ? 'hidden' : 'legacy';
      }
      if (els.aiSurfaceScenarioGrid) {
        els.aiSurfaceScenarioGrid.innerHTML = AI_SURFACE_SCENARIO_IDS.map((scenarioId) => {
          const scenario = scenarioRegistry[scenarioId] && typeof scenarioRegistry[scenarioId] === 'object'
            ? scenarioRegistry[scenarioId]
            : {};
          const scenarioModeState = scenarioStateMap[scenarioId] && typeof scenarioStateMap[scenarioId] === 'object'
            ? scenarioStateMap[scenarioId]
            : {};
          const entryId = aiSurfaceScenarioEntryId(scenarioId);
          const entryExposure = entryId && entryExposureMap[entryId] && typeof entryExposureMap[entryId] === 'object'
            ? entryExposureMap[entryId]
            : null;
          const exposureState = String(
            entryExposure?.exposure_state
            || scenarioModeState.rollout_state
            || scenario.rollout_state
            || 'hidden'
          ).trim().toLowerCase();
          const stateTone = aiSurfaceExposureTone(exposureState);
          const stateLabel = aiSurfaceExposureLabel(exposureState);
          const isSelected = scenarioId === selectedScenario;
          const title = aiSurfaceScenarioTitle(scenarioId, scenario);
          const description = aiSurfaceScenarioDescription(scenarioId);
          const meta = aiSurfaceScenarioUserMeta(scenarioId) || aiSurfaceScenarioMeta(scenario);
          return ''
            + '<button class="ai-entry-tile' + (isSelected ? ' is-selected' : '') + '" type="button" data-scenario="' + escapeHtml(scenarioId) + '" data-state="' + escapeHtml(stateTone) + '">'
              + '<span class="ai-entry-tile__title">' + escapeHtml(title) + '</span>'
              + '<span class="ai-entry-tile__description">' + escapeHtml(description) + '</span>'
              + '<span class="ai-entry-tile__meta">' + escapeHtml(meta || aiSurfaceScenarioMeta(scenario)) + '</span>'
              + '<span class="ai-entry-tile__state">' + escapeHtml(stateLabel) + '</span>'
            + '</button>';
        }).join('');
      }
      if (els.aiSurfaceResult) {
        const scenario = selectedScenarioRegistry;
        const scenarioMode = selectedModeState;
        const entryId = selectedEntryId;
        const entryExposure = entryId && entryExposureMap[entryId] && typeof entryExposureMap[entryId] === 'object'
          ? entryExposureMap[entryId]
          : null;
        const sourceList = Array.isArray(scenario.context_sources) ? scenario.context_sources.map((item) => String(item || '').trim()).filter(Boolean).join(' · ') : '';
        const boundaryList = Array.isArray(scenario.boundaries) ? scenario.boundaries.map((item) => String(item || '').trim()).filter(Boolean).join(' · ') : '';
        const nonGoalList = Array.isArray(scenario.non_goals) ? scenario.non_goals.map((item) => String(item || '').trim()).filter(Boolean).join(' · ') : '';
        const entrySurfaces = Array.isArray(scenario.allowed_entry_surfaces) ? scenario.allowed_entry_surfaces.map((item) => String(item || '').trim()).filter(Boolean).join(' · ') : '';
        if (selectedScenario === 'full_card_enrichment') {
          const enrichmentResult = renderFullCardEnrichmentResult(payload, scenario, selectedExposureLabel);
          els.aiSurfaceResult.dataset.state = enrichmentResult.state;
          if (enrichmentResult.tone) els.aiSurfaceResult.dataset.tone = enrichmentResult.tone;
          else delete els.aiSurfaceResult.dataset.tone;
          els.aiSurfaceResult.innerHTML = enrichmentResult.html;
          return;
        }
        const detailParts = [];
        if (selectedScenario === 'ai_chat') {
          detailParts.push('<strong>Чат с AI</strong>');
          detailParts.push('Открывает отдельное окно для ручной работы с вопросами и CRM-контекстом.');
          detailParts.push('Статус: ' + escapeHtml(selectedExposureLabel) + '.');
          detailParts.push('Использует: ' + escapeHtml(sourceList || 'CRM-контекст.'));
          detailParts.push('Это новый основной ручной путь, а не старое меню агента.');
        } else if (selectedScenario === 'board_control') {
          detailParts.push('<strong>Фоновый контроль доски</strong>');
          detailParts.push('Этот режим работает тихо в фоне и не предназначен для ручного запуска оператором.');
          detailParts.push('Статус: ' + escapeHtml(selectedExposureLabel) + '.');
          detailParts.push('Использует: ' + escapeHtml(sourceList || 'delta-board context.'));
          detailParts.push('Границы: ' + escapeHtml(boundaryList || 'только безопасные изменения.'));
        } else {
          detailParts.push('<strong>' + escapeHtml(aiSurfaceScenarioTitle(selectedScenario, scenario)) + '</strong>');
          detailParts.push('Статус: ' + escapeHtml(selectedExposureLabel) + '.');
          detailParts.push('Контекст: ' + escapeHtml(sourceList || 'нет'));
          detailParts.push('Границы: ' + escapeHtml(boundaryList || 'нет'));
          detailParts.push('Не делает: ' + escapeHtml(nonGoalList || 'нет'));
        }
        els.aiSurfaceResult.innerHTML = '<div class="agent-result__fallback">' + detailParts.join('<br>') + '</div>';
      }
    }

    function openAiSurface(kind = 'chat') {
      if (!requireOperatorSession()) return;
      hydrateAiSurfaceUiRefs();
      bindAiSurfaceUiEvents();
      closeAiChatWindow();
      closeAgentModal();
      state.aiSurfaceContext = buildAiSurfaceContext(kind);
      const normalizedKind = String(kind || '').trim().toLowerCase();
      state.aiSurfaceSelectedScenario = normalizedKind === 'card'
        ? 'full_card_enrichment'
        : (normalizedKind === 'board' ? 'board_control' : 'ai_chat');
      if (els.aiSurfaceModal) {
        els.aiSurfaceModal.classList.add('is-open');
      }
      renderAiEntrySurface(state.aiSurfaceStatusPayload || state.agentStatusPayload || {});
      refreshAgentModalState();
    }

    function closeAiSurface() {
      els.aiSurfaceModal?.classList.remove('is-open');
      if (!els.agentModal?.classList.contains('is-open')) {
        if (state.agentRefreshTimer) {
          window.clearTimeout(state.agentRefreshTimer);
          state.agentRefreshTimer = null;
        }
      }
    }

    function summarizeAgentText(value, maxLength = 140) {
      const text = String(value || '').replace(/\\s+/g, ' ').trim();
      if (!text) return '';
      if (text.length <= maxLength) return text;
      return text.slice(0, Math.max(0, maxLength - 1)).trimEnd() + '…';
    }

    function formatAgentErrorMessage(rawValue) {
      const raw = String(rawValue || '').trim();
      if (!raw) return 'Агент завершил задачу с ошибкой.';
      const normalized = raw.toLowerCase();
      if (normalized.includes('unsupported_country_region_territory')) {
        return 'OpenAI API недоступен из текущего региона сервера.';
      }
      if (normalized.includes('http 403')) {
        return 'Внешний сервис отклонил запрос агента (403).';
      }
      if (normalized.includes('timed out') || normalized.includes('timeout')) {
        return 'Агент не дождался ответа внешнего сервиса.';
      }
      if (normalized.includes('network') || normalized.includes('connection')) {
        return 'Ошибка сетевого доступа у агента.';
      }
      return summarizeAgentText(raw, 220);
    }

    function normalizeAgentDisplay(task) {
      const rawDisplay = task?.display && typeof task.display === 'object' ? task.display : {};
      const title = String(rawDisplay.title || task?.summary || '').trim();
      const summary = String(rawDisplay.summary || '').trim();
      const emoji = String(rawDisplay.emoji || '').trim().slice(0, 6);
      const tone = String(rawDisplay.tone || '').trim().toLowerCase();
      const sections = Array.isArray(rawDisplay.sections) ? rawDisplay.sections : [];
      const actions = Array.isArray(rawDisplay.actions) ? rawDisplay.actions : [];
      if (title || summary || sections.length || actions.length) {
        return {
          emoji,
          title,
          summary,
          tone: ['info', 'success', 'warning', 'error'].includes(tone) ? tone : 'success',
          sections: sections
            .filter((item) => item && typeof item === 'object')
            .map((item) => ({
              title: String(item.title || '').trim(),
              body: String(item.body || '').trim(),
              items: Array.isArray(item.items)
                ? item.items.map((entry) => String(entry || '').trim()).filter(Boolean)
                : [],
            }))
            .filter((item) => item.title || item.body || item.items.length),
          actions: actions.map((item) => String(item || '').trim()).filter(Boolean),
        };
      }
      const summaryText = String(task?.summary || '').trim();
      const resultText = String(task?.result || '').trim();
      const blocks = resultText ? resultText.split(/\\n\\s*\\n/).map((item) => item.trim()).filter(Boolean) : [];
      const lead = blocks.length ? blocks.shift() : resultText;
      const parsedSections = blocks.map((block) => {
        const lines = block.split('\\n').map((item) => item.trim()).filter(Boolean);
        if (!lines.length) return null;
        const titleLine = lines[0].endsWith(':') ? lines[0].slice(0, -1).trim() : '';
        const bulletLines = lines
          .filter((line) => /^[-•]/.test(line))
          .map((line) => line.replace(/^[-•]\\s*/, '').trim())
          .filter(Boolean);
        const bodyLines = titleLine
          ? lines.slice(1).filter((line) => !/^[-•]/.test(line))
          : lines.filter((line) => !/^[-•]/.test(line));
        if (!titleLine && !bulletLines.length) return { title: '', body: lines.join(' '), items: [] };
        return {
          title: titleLine,
          body: bodyLines.join(' ').trim(),
          items: bulletLines,
        };
      }).filter(Boolean);
      return {
        emoji: '',
        title: summaryText,
        summary: lead,
        tone: 'success',
        sections: parsedSections,
        actions: [],
      };
    }

    function renderAgentDisplay(display) {
      const payload = display && typeof display === 'object' ? display : {};
      const title = String(payload.title || '').trim();
      const summary = String(payload.summary || '').trim();
      const emoji = String(payload.emoji || '').trim();
      const sections = Array.isArray(payload.sections) ? payload.sections : [];
      const actions = Array.isArray(payload.actions) ? payload.actions : [];
      const hero = (emoji || title || summary)
        ? '<div class="agent-result__hero">'
          + ((emoji || title)
            ? '<div class="agent-result__hero-line">'
              + (emoji ? '<div class="agent-result__emoji">' + escapeHtml(emoji) + '</div>' : '')
              + (title ? '<div class="agent-result__title">' + escapeHtml(title) + '</div>' : '')
              + '</div>'
            : '')
          + (summary ? '<div class="agent-result__summary">' + escapeHtml(summary) + '</div>' : '')
          + '</div>'
        : '';
      const sectionsHtml = sections.length
        ? '<div class="agent-result__sections">' + sections.map((section) =>
            '<section class="agent-result__section">'
              + (section.title ? '<div class="agent-result__section-title">' + escapeHtml(section.title) + '</div>' : '')
              + (section.body ? '<div class="agent-result__section-body">' + escapeHtml(section.body) + '</div>' : '')
              + (Array.isArray(section.items) && section.items.length
                ? '<ul class="agent-result__list">' + section.items.map((item) => '<li>' + escapeHtml(item) + '</li>').join('') + '</ul>'
                : '')
              + '</section>'
          ).join('') + '</div>'
        : '';
      const actionsHtml = actions.length
        ? '<div class="agent-result__actions">' + actions.map((item) =>
            '<button class="agent-result__action" type="button" data-agent-follow-up="' + escapeHtml(item) + '">' + escapeHtml(item) + '</button>'
          ).join('') + '</div>'
        : '';
      return hero + sectionsHtml + actionsHtml;
    }

    function renderAgentStatus(statusPayload) {
      const payload = statusPayload && typeof statusPayload === 'object' ? statusPayload : {};
      const status = payload.status && typeof payload.status === 'object' ? payload.status : {};
      const queue = payload.queue && typeof payload.queue === 'object' ? payload.queue : {};
      const recentRuns = Array.isArray(payload.recent_runs) ? payload.recent_runs : [];
      const latestRun = recentRuns.length ? recentRuns[0] : null;
      const latestRunStatus = String(latestRun?.status || '').trim().toLowerCase();
      const agentReady = Boolean(payload.agent?.ready ?? payload.agent?.available ?? payload.agent?.enabled);
      const availabilityReason = String(payload.agent?.availability_reason || '').trim().toLowerCase();
      let stateLabel = 'ОФЛАЙН';
      let stateValue = 'idle';
      if (agentReady) {
        stateLabel = 'ГОТОВ';
        stateValue = 'online';
      } else if (availabilityReason === 'configured_but_worker_idle') {
        stateLabel = 'ЗАПУСК';
        stateValue = 'waiting';
      }
      if (status.running) {
        stateLabel = 'В РАБОТЕ';
        stateValue = 'busy';
      } else if (latestRunStatus === 'completed') {
        stateLabel = 'ГОТОВ';
        stateValue = 'online';
      } else if (latestRunStatus === 'failed') {
        stateLabel = 'СБОЙ';
        stateValue = 'error';
      } else if (status.last_error) {
        stateLabel = 'СБОЙ';
        stateValue = 'error';
      }
      if (els.agentStatusLabel) {
        const pendingTotal = Number(queue.pending_total || 0);
        els.agentStatusLabel.textContent = pendingTotal > 0 ? (stateLabel + ' · ' + pendingTotal) : stateLabel;
        els.agentStatusLabel.dataset.state = stateValue;
      }
      if (els.agentRunButton) {
        const busy = Boolean(status.running);
        els.agentRunButton.disabled = busy;
        els.agentRunButton.textContent = busy ? 'ВЫПОЛНЯЕТСЯ' : 'ВЫПОЛНИТЬ';
      }
      renderAgentAutofillControls(payload);
    }

    function renderAgentAutofillControls(statusPayload) {
      const payload = statusPayload && typeof statusPayload === 'object' ? statusPayload : {};
      const agentReady = Boolean(payload.agent?.ready ?? payload.agent?.available ?? payload.agent?.enabled);
      const availabilityReason = String(payload.agent?.availability_reason || '').trim().toLowerCase();
      const card = currentAgentContextCard();
      const activeTask = currentCardAutofillTask(state.agentLatestTasks);
      const active = Boolean(card?.ai_autofill_active);
      const displayActive = Boolean(active || activeTask);
      const untilText = String(card?.ai_autofill_until || '').trim();
      const countdown = displayActive ? formatAgentCountdown(untilText) : '';
      let buttonLabel = displayActive ? 'АВТО' : 'АВТОЗАПОЛНЕНИЕ';
      let statusText = 'ОТКРОЙ КАРТОЧКУ';
      let stateValue = 'offline';
      let disabled = !String(card?.id || '').trim();
      if (String(card?.id || '').trim()) {
        if (activeTask) {
          const trigger = String(activeTask?.metadata?.trigger || '').trim().toLowerCase();
          statusText = (trigger === 'adaptive_followup' || trigger === 'retry_after_error') ? 'ПОВТОРНЫЙ ПРОХОД' : 'ПЕРВЫЙ ПРОХОД';
          stateValue = 'active';
          disabled = false;
        } else if (active) {
          const nextRunText = formatAgentClock(card?.ai_next_run_at || '');
          statusText = nextRunText ? ('ОЖИДАНИЕ · ' + nextRunText) : 'АВТОСОПРОВОЖДЕНИЕ АКТИВНО';
          stateValue = 'waiting';
          buttonLabel = 'АВТО';
          disabled = false;
        } else if (!agentReady && availabilityReason === 'configured_but_worker_idle') {
          statusText = 'SERVER AI STARTING';
          stateValue = 'waiting';
          disabled = true;
        } else if (!agentReady) {
          statusText = 'SERVER AI OFFLINE';
          stateValue = 'offline';
          disabled = true;
        } else {
          statusText = 'SERVER AI READY';
          stateValue = 'online';
          disabled = false;
        }
      }
      if (els.agentAutofillButton) {
        els.agentAutofillButton.innerHTML = displayActive
          ? '<span class="agent-autofill-button__label">' + escapeHtml(buttonLabel) + '</span><span class="agent-autofill-button__timer">' + escapeHtml(countdown || '00:00') + '</span>'
          : '<span class="agent-autofill-button__label">' + escapeHtml(buttonLabel) + '</span>';
        els.agentAutofillButton.disabled = disabled;
        els.agentAutofillButton.dataset.state = displayActive ? 'active' : 'inactive';
      }
      if (els.agentAutofillStatus) {
        els.agentAutofillStatus.textContent = statusText;
        els.agentAutofillStatus.dataset.state = stateValue;
      }
      syncAgentAutofillPromptPanel(card);
    }

    function syncAgentAutofillPromptPanel(card) {
      const promptValue = String(card?.ai_autofill_prompt || '').trim();
      if (els.agentAutofillPromptInput && document.activeElement !== els.agentAutofillPromptInput) {
        els.agentAutofillPromptInput.value = promptValue;
      }
      if (els.agentAutofillPromptPanel) {
        els.agentAutofillPromptPanel.hidden = !state.agentAutofillPromptOpen;
      }
      if (els.agentAutofillPromptToggle) {
        els.agentAutofillPromptToggle.dataset.open = state.agentAutofillPromptOpen ? 'true' : 'false';
      }
    }

    function toggleAgentAutofillPromptPanel() {
      state.agentAutofillPromptOpen = !state.agentAutofillPromptOpen;
      syncAgentAutofillPromptPanel(currentAgentContextCard());
      if (state.agentAutofillPromptOpen) {
        window.setTimeout(() => els.agentAutofillPromptInput?.focus(), 0);
      }
    }

    async function saveAgentAutofillPrompt() {
      const card = currentAgentContextCard();
      const cardId = String(card?.id || '').trim();
      if (!cardId) return setStatus('ОТКРОЙ КАРТОЧКУ ДЛЯ MINI-PROMPT.', true);
      try {
        const data = await api('/api/set_card_ai_autofill', {
          method: 'POST',
          body: {
            card_id: cardId,
            prompt: String(els.agentAutofillPromptInput?.value || '').trim(),
            actor_name: state.actor,
          },
        });
        if (data?.card) {
          state.activeCard = data.card;
          if (els.cardModal?.classList.contains('is-open')) applyCardModalState(data.card);
        }
        setStatus('MINI-PROMPT СОХРАНЁН.', false);
        await refreshAgentModalState();
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function resetAgentAutofillPrompt() {
      if (els.agentAutofillPromptInput) els.agentAutofillPromptInput.value = '';
      await saveAgentAutofillPrompt();
    }

    async function toggleAgentCardAutofill() {
      const card = currentAgentContextCard();
      const cardId = String(card?.id || '').trim();
      if (!cardId) return setStatus('ОТКРОЙ КАРТОЧКУ ДЛЯ АВТОЗАПОЛНЕНИЯ.', true);
      const nextEnabled = !(Boolean(card?.ai_autofill_active) || Boolean(currentCardAutofillTask(state.agentLatestTasks)));
      try {
        if (els.agentAutofillButton) els.agentAutofillButton.disabled = true;
        const data = await api('/api/set_card_ai_autofill', {
          method: 'POST',
          body: {
            card_id: cardId,
            enabled: nextEnabled,
            prompt: String(els.agentAutofillPromptInput?.value || card?.ai_autofill_prompt || '').trim(),
            actor_name: state.actor,
          },
        });
        if (data?.card) {
          state.activeCard = data.card;
          if (els.cardModal?.classList.contains('is-open')) applyCardModalState(data.card);
        }
        if (data?.meta?.task_id) {
          state.agentTaskId = String(data.meta.task_id || '').trim();
          state.agentLatestTasks = [
            {
              id: state.agentTaskId,
              status: 'running',
              metadata: {
                purpose: 'card_autofill',
                trigger: 'manual_activate',
                context: { kind: 'card', card_id: cardId },
              },
            },
            ...((Array.isArray(state.agentLatestTasks) ? state.agentLatestTasks : []).filter((item) => String(item?.id || '').trim() !== state.agentTaskId)),
          ];
        } else if (!nextEnabled) {
          state.agentLatestTasks = (Array.isArray(state.agentLatestTasks) ? state.agentLatestTasks : []).filter((item) => {
            const metadata = item?.metadata && typeof item.metadata === 'object' ? item.metadata : {};
            const context = metadata.context && typeof metadata.context === 'object' ? metadata.context : {};
            return !(String(metadata.purpose || '').trim().toLowerCase() === 'card_autofill'
              && String(context.card_id || '').trim() === cardId);
          });
        }
        if (data?.meta && Object.prototype.hasOwnProperty.call(data.meta, 'server_available')) {
          const payload = state.agentStatusPayload && typeof state.agentStatusPayload === 'object' ? state.agentStatusPayload : {};
          const agent = payload.agent && typeof payload.agent === 'object' ? payload.agent : {};
          const serverAvailable = Boolean(data.meta.server_available);
          state.agentStatusPayload = {
            ...payload,
            agent: {
              ...agent,
              available: serverAvailable,
              ready: serverAvailable ? Boolean(agent.ready ?? true) : Boolean(agent.ready ?? false),
              availability_reason: serverAvailable
                ? String(agent.availability_reason || 'worker_running')
                : String(agent.availability_reason || 'configured_but_worker_idle'),
            },
          };
        }
        renderAgentAutofillControls(state.agentStatusPayload || {});
        setStatus(nextEnabled ? 'АВТОСОПРОВОЖДЕНИЕ ВКЛЮЧЕНО НА 4 ЧАСА.' : 'АВТОСОПРОВОЖДЕНИЕ ОТКЛЮЧЕНО.', false);
        await refreshAgentModalState();
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        renderAgentAutofillControls(state.agentStatusPayload || {});
      }
    }

    function renderAgentActions(actions) {
      const items = Array.isArray(actions) ? actions : [];
      if (!els.agentActionsList) return;
      if (!items.length) {
        els.agentActionsList.innerHTML = '<div class="cashboxes-empty">Действий пока нет.</div>';
        return;
      }
      els.agentActionsList.innerHTML = items.map((item) => {
        const tool = String(item?.tool || '').trim() || 'tool';
        const reason = String(item?.reason || '').trim() || 'Без пояснения';
        const finishedAt = String(item?.finished_at || '').trim() || 'нет времени';
        return '<div class="agent-action-row">'
          + '<div class="agent-action-row__tool">' + escapeHtml(tool) + '</div>'
          + '<div class="agent-action-row__reason">' + escapeHtml(reason) + '</div>'
          + '<div class="agent-action-row__meta">' + escapeHtml(finishedAt) + '</div>'
          + '</div>';
      }).join('');
    }

    function renderAgentQuickActions(context) {
      if (!els.agentQuickActions) return;
      const actions = quickAgentPrompts(context);
      els.agentQuickActions.innerHTML = actions.map((item) =>
        '<button class="agent-shortcut" type="button"'
          + (item.action ? ' data-agent-open="' + escapeHtml(item.action) + '"' : '')
          + (item.template ? ' data-agent-template="' + escapeHtml(item.template) + '"' : '')
          + (item.prompt ? ' data-agent-prompt="' + escapeHtml(item.prompt) + '"' : '')
          + '>' + escapeHtml(item.label) + '</button>'
      ).join('');
    }

    function scheduleAgentTasksRefresh(delay = 4000) {
      if (state.agentTasksRefreshTimer) window.clearTimeout(state.agentTasksRefreshTimer);
      if (!els.agentTasksModal?.classList.contains('is-open')) return;
      state.agentTasksRefreshTimer = window.setTimeout(refreshAgentTasksModalState, delay);
    }

    function renderAgentScheduledColumns(columns) {
      const items = Array.isArray(columns) ? columns : [];
      const selected = String(els.agentTaskScopeColumnInput?.value || '').trim();
      if (!els.agentTaskScopeColumnInput) return;
      els.agentTaskScopeColumnInput.innerHTML = items.length
        ? items.map((item) => '<option value="' + escapeHtml(item.id) + '"' + (item.id === selected ? ' selected' : '') + '>' + escapeHtml(item.label || item.id) + '</option>').join('')
        : '<option value="">Колонок нет</option>';
    }

    function currentAgentContextCard() {
      const context = activeAiTaskContext();
      if (String(context.kind || '').trim().toLowerCase() !== 'card') return null;
      const activeCard = state.activeCard && typeof state.activeCard === 'object' ? state.activeCard : null;
      if (activeCard && String(activeCard.id || '').trim()) return activeCard;
      return {
        id: String(context.card_id || state.editingId || '').trim(),
        heading: String(context.card_heading || '').trim(),
        title: String(context.card_title || '').trim(),
      };
    }

    function defaultAgentScheduledScope() {
      const card = currentAgentContextCard();
      if (!card || !String(card.id || '').trim()) {
        return {
          scopeType: 'all_cards',
          scopeCardId: '',
          scopeCardLabel: '',
          prompt: '',
        };
      }
      return {
        scopeType: 'current_card',
        scopeCardId: String(card.id || '').trim(),
        scopeCardLabel: String(card.heading || card.title || card.id || '').trim(),
        prompt: String(els.agentTaskInput?.value || '').trim(),
      };
    }

    function syncAgentScheduledTaskFormUi() {
      const scopeType = String(els.agentTaskScopeTypeInput?.value || 'all_cards').trim().toLowerCase();
      const scheduleType = String(els.agentTaskScheduleTypeInput?.value || 'once').trim().toLowerCase();
      if (els.agentTaskScopeColumnInput) els.agentTaskScopeColumnInput.disabled = scopeType !== 'column';
      if (els.agentTaskIntervalValueInput) els.agentTaskIntervalValueInput.disabled = scheduleType !== 'interval';
      if (els.agentTaskIntervalUnitInput) els.agentTaskIntervalUnitInput.disabled = scheduleType !== 'interval';
      if (scopeType === 'current_card') {
        const defaults = defaultAgentScheduledScope();
        state.agentTaskScopeCardId = defaults.scopeCardId;
        state.agentTaskScopeCardLabel = defaults.scopeCardLabel;
      }
      if (els.agentTaskRunButton) els.agentTaskRunButton.disabled = !String(state.agentScheduledActiveId || '').trim();
    }

    function activeAgentScheduledTask() {
      return (state.agentScheduledTasks || []).find((item) => String(item?.id || '') === String(state.agentScheduledActiveId || '')) || null;
    }

    function agentScheduledTaskStatusLabel(task) {
      return Boolean(task?.active) ? 'ACTIVE' : 'PAUSED';
    }

    function agentScheduledTaskPeriodLabel(task) {
      const scheduleType = String(task?.schedule_type || 'once').trim().toLowerCase();
      if (scheduleType === 'on_create') return 'ON CREATE';
      if (scheduleType !== 'interval') return 'ОДИН РАЗ';
      const value = Math.max(1, Number(task?.interval_value || 1) || 1);
      return value + ' ' + (String(task?.interval_unit || 'minute').trim().toLowerCase() === 'hour' ? 'Ч' : 'МИН');
    }

    function agentScheduledTaskScopeLabel(task) {
      return String(
        task?.scope_type === 'current_card'
          ? (task?.scope_card_label || task?.scope_card_id || 'Текущая карточка')
          : task?.scope_type === 'column'
          ? (task?.scope_label || task?.scope_column || 'Колонка')
          : 'Все карточки'
      ).trim();
    }

    function agentScheduledTaskTimingLabel(task) {
      const parts = [];
      if (Boolean(task?.busy)) parts.push('В РАБОТЕ');
      if (task?.next_run_at) parts.push('СЛЕДУЮЩИЙ: ' + formatDate(task.next_run_at));
      else if (task?.last_enqueued_at) parts.push('ПОСЛЕДНИЙ: ' + formatDate(task.last_enqueued_at));
      else parts.push('ЕЩЁ НЕ ЗАПУСКАЛАСЬ');
      return parts.join(' · ');
    }

    function agentScheduledTaskFormMetaText(task) {
      if (!task) return 'Один запрос, текущая карточка, одна колонка или вся доска. Запуск вручную, по интервалу или при создании.';
      const parts = [
        agentScheduledTaskStatusLabel(task),
        agentScheduledTaskPeriodLabel(task),
        'ОХВАТ: ' + agentScheduledTaskScopeLabel(task).toUpperCase(),
      ];
      if (Boolean(task?.busy)) parts.push('В РАБОТЕ');
      if (task?.next_run_at) parts.push('СЛЕДУЮЩИЙ: ' + formatDate(task.next_run_at));
      else if (task?.last_enqueued_at) parts.push('ПОСЛЕДНИЙ: ' + formatDate(task.last_enqueued_at));
      return parts.join(' · ');
    }

    function resetAgentScheduledTaskForm() {
      const defaults = defaultAgentScheduledScope();
      state.agentScheduledActiveId = '';
      state.agentTaskScopeCardId = defaults.scopeCardId;
      state.agentTaskScopeCardLabel = defaults.scopeCardLabel;
      if (els.agentTasksEditorTitle) els.agentTasksEditorTitle.textContent = 'НОВАЯ ЗАДАЧА';
      if (els.agentTaskNameInput) els.agentTaskNameInput.value = '';
      if (els.agentTaskPromptInput) els.agentTaskPromptInput.value = defaults.prompt || '';
      if (els.agentTaskScopeTypeInput) els.agentTaskScopeTypeInput.value = defaults.scopeType;
      renderAgentScheduledColumns(state.agentScheduledColumns || []);
      if (els.agentTaskScheduleTypeInput) els.agentTaskScheduleTypeInput.value = 'once';
      if (els.agentTaskIntervalValueInput) els.agentTaskIntervalValueInput.value = '1';
      if (els.agentTaskIntervalUnitInput) els.agentTaskIntervalUnitInput.value = 'minute';
      if (els.agentTaskActiveInput) els.agentTaskActiveInput.checked = true;
      if (els.agentTaskFormMeta) els.agentTaskFormMeta.textContent = agentScheduledTaskFormMetaText(null);
      syncAgentScheduledTaskFormUi();
      renderAgentScheduledTasks(state.agentScheduledTasks || []);
    }

    function applyAgentScheduledTaskToForm(task) {
      if (!task) {
        resetAgentScheduledTaskForm();
        return;
      }
      state.agentScheduledActiveId = String(task.id || '');
      state.agentTaskScopeCardId = String(task.scope_card_id || '').trim();
      state.agentTaskScopeCardLabel = String(task.scope_card_label || '').trim();
      if (els.agentTasksEditorTitle) els.agentTasksEditorTitle.textContent = 'РЕДАКТИРОВАНИЕ';
      if (els.agentTaskNameInput) els.agentTaskNameInput.value = String(task.name || '');
      if (els.agentTaskPromptInput) els.agentTaskPromptInput.value = String(task.prompt || '');
      if (els.agentTaskScopeTypeInput) els.agentTaskScopeTypeInput.value = String(task.scope_type || 'all_cards');
      renderAgentScheduledColumns(state.agentScheduledColumns || []);
      if (els.agentTaskScopeColumnInput) els.agentTaskScopeColumnInput.value = String(task.scope_column || '');
      if (els.agentTaskScheduleTypeInput) els.agentTaskScheduleTypeInput.value = String(task.schedule_type || 'once');
      if (els.agentTaskIntervalValueInput) els.agentTaskIntervalValueInput.value = String(task.interval_value || 1);
      if (els.agentTaskIntervalUnitInput) els.agentTaskIntervalUnitInput.value = String(task.interval_unit || 'minute');
      if (els.agentTaskActiveInput) els.agentTaskActiveInput.checked = Boolean(task.active);
      if (els.agentTaskFormMeta) els.agentTaskFormMeta.textContent = agentScheduledTaskFormMetaText(task);
      syncAgentScheduledTaskFormUi();
      renderAgentScheduledTasks(state.agentScheduledTasks || []);
    }

    function renderAgentScheduledTasks(tasks) {
      if (!els.agentTasksList) return;
      const items = Array.isArray(tasks) ? tasks : [];
      if (!items.length) {
        els.agentTasksList.innerHTML = ''
          + '<div class="agent-tasks-empty">'
          + '<div class="agent-tasks-empty__title">Задач пока нет</div>'
          + '<div class="agent-tasks-empty__text">Создай первую задачу справа: опиши цель, выбери всю доску или одну колонку и сохрани расписание.</div>'
          + '</div>';
        return;
      }
      els.agentTasksList.innerHTML = items.slice(0, 50).map((task) => {
        const status = String(task.status || 'paused').trim().toLowerCase();
        const scopeLabel = agentScheduledTaskScopeLabel(task);
        const promptPreview = summarizeAgentText(String(task.prompt || ''), 148) || 'Без описания.';
        const warning = summarizeAgentText(String(task.last_error || ''), 160);
        return '<div class="agent-task-row" data-agent-scheduled-task-id="' + escapeHtml(task.id) + '" data-active="' + String(String(task.id) === String(state.agentScheduledActiveId || '')) + '" data-busy="' + String(Boolean(task.busy)) + '">'
          + '<div class="agent-task-row__top">'
            + '<div class="agent-task-row__main">'
              + '<div class="agent-task-row__title">' + escapeHtml(task.name || 'Задача') + '</div>'
              + '<div class="agent-task-row__meta">' + escapeHtml(scopeLabel.toUpperCase()) + '</div>'
            + '</div>'
            + '<div class="agent-task-actions">'
              + '<button class="btn btn--ghost" type="button" data-agent-scheduled-action="run" data-task-id="' + escapeHtml(task.id) + '">?</button>'
              + '<button class="btn btn--ghost" type="button" data-agent-scheduled-action="' + escapeHtml(task.active ? 'pause' : 'resume') + '" data-task-id="' + escapeHtml(task.id) + '">' + escapeHtml(task.active ? '?' : '?') + '</button>'
              + '<button class="btn btn--ghost" type="button" data-agent-scheduled-action="delete" data-task-id="' + escapeHtml(task.id) + '">?</button>'
            + '</div>'
          + '</div>'
          + '<div class="agent-task-row__prompt">' + escapeHtml(promptPreview) + '</div>'
          + '<div class="agent-task-row__footer">'
            + '<div class="agent-task-row__chips">'
              + '<div class="agent-task-chip" data-status="' + escapeHtml(status) + '">' + escapeHtml(agentScheduledTaskStatusLabel(task)) + '</div>'
              + '<div class="agent-task-chip">' + escapeHtml(agentScheduledTaskPeriodLabel(task)) + '</div>'
            + '</div>'
            + '<div class="agent-task-row__timing">' + escapeHtml(agentScheduledTaskTimingLabel(task)) + '</div>'
          + '</div>'
          + (warning ? '<div class="agent-task-row__warning">' + escapeHtml(warning) + '</div>' : '')
        + '</div>';
      }).join('');
    }

    function readAgentScheduledTaskPayload() {
      const scopeType = String(els.agentTaskScopeTypeInput?.value || 'all_cards').trim();
      return {
        task_id: String(state.agentScheduledActiveId || '').trim(),
        name: String(els.agentTaskNameInput?.value || '').trim(),
        prompt: String(els.agentTaskPromptInput?.value || '').trim(),
        scope_type: scopeType,
        scope_column: String(els.agentTaskScopeColumnInput?.value || '').trim(),
        scope_column_label: String(els.agentTaskScopeColumnInput?.selectedOptions?.[0]?.textContent || '').trim(),
        scope_card_id: scopeType === 'current_card' ? String(state.agentTaskScopeCardId || '').trim() : '',
        scope_card_label: scopeType === 'current_card' ? String(state.agentTaskScopeCardLabel || '').trim() : '',
        schedule_type: String(els.agentTaskScheduleTypeInput?.value || 'once').trim(),
        interval_value: Number(els.agentTaskIntervalValueInput?.value || 1) || 1,
        interval_unit: String(els.agentTaskIntervalUnitInput?.value || 'minute').trim(),
        active: Boolean(els.agentTaskActiveInput?.checked),
      };
    }

    async function refreshAgentTasksModalState() {
      if (!els.agentTasksModal?.classList.contains('is-open')) return;
      try {
        const [tasksData, columnsData, statusData] = await Promise.all([
          api('/api/agent_scheduled_tasks', { method: 'GET' }),
          api('/api/list_columns', { method: 'GET' }),
          api('/api/agent_status', { method: 'GET' }),
        ]);
        state.agentScheduledTasks = Array.isArray(tasksData?.tasks) ? tasksData.tasks : [];
        state.agentScheduledColumns = Array.isArray(columnsData?.columns) ? columnsData.columns : (state.snapshot?.columns || []);
        renderAgentScheduledColumns(state.agentScheduledColumns);
        renderAgentScheduledTasks(state.agentScheduledTasks);
        const activeTotal = Number(statusData?.scheduled?.active_total || 0);
        const pausedTotal = Number(statusData?.scheduled?.paused_total || 0);
        const total = state.agentScheduledTasks.length;
        const busyTotal = state.agentScheduledTasks.filter((item) => Boolean(item?.busy)).length;
        if (els.agentTasksMeta) {
          els.agentTasksMeta.textContent = 'ВСЕГО ' + total + ' · АКТИВНЫХ ' + activeTotal + ' · ПАУЗА ' + pausedTotal + (busyTotal ? ' · В РАБОТЕ ' + busyTotal : '');
        }
        const activeTask = activeAgentScheduledTask();
        if (activeTask) applyAgentScheduledTaskToForm(activeTask);
        else resetAgentScheduledTaskForm();
        scheduleAgentTasksRefresh(state.agentScheduledTasks.some((item) => item?.busy) ? 1800 : 4000);
      } catch (error) {
        if (els.agentTasksMeta) els.agentTasksMeta.textContent = String(error.message || 'Ошибка загрузки.').toUpperCase();
        scheduleAgentTasksRefresh(5000);
      }
    }

    function openAgentTasksModal() {
      if (!requireOperatorSession()) return;
      ensureAgentTasksUi();
      bindAgentTasksUiEvents();
      hydrateAgentTasksUiRefs();
      els.agentTasksModal?.classList.add('is-open');
      refreshAgentTasksModalState();
    }

    function closeAgentTasksModal() {
      els.agentTasksModal?.classList.remove('is-open');
      if (state.agentTasksRefreshTimer) {
        window.clearTimeout(state.agentTasksRefreshTimer);
        state.agentTasksRefreshTimer = null;
      }
    }

    async function saveAgentScheduledTask() {
      const payload = readAgentScheduledTaskPayload();
      if (!payload.name) return setStatus('УКАЖИ НАЗВАНИЕ ЗАДАЧИ.', true);
      if (!payload.prompt) return setStatus('ОПИШИ ЗАДАЧУ ДЛЯ АГЕНТА.', true);
      if (payload.scope_type === 'column' && !payload.scope_column) return setStatus('ВЫБЕРИ КОЛОНКУ.', true);
      if (payload.scope_type === 'current_card' && !payload.scope_card_id) return setStatus('ОТКРОЙ КАРТОЧКУ ИЛИ ВЫБЕРИ ДРУГОЙ ОХВАТ.', true);
      try {
        const data = await api('/api/save_agent_scheduled_task', { method: 'POST', body: payload });
        state.agentScheduledActiveId = String(data?.task?.id || payload.task_id || '');
        setStatus('ЗАДАЧА СОХРАНЕНА.', false);
        await refreshAgentTasksModalState();
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function runActiveAgentScheduledTask() {
      const taskId = String(state.agentScheduledActiveId || '').trim();
      if (!taskId) return setStatus('СНАЧАЛА ВЫБЕРИ ЗАДАЧУ.', true);
      try {
        const data = await api('/api/run_agent_scheduled_task', { method: 'POST', body: { task_id: taskId } });
        setStatus(data?.meta?.already_running ? 'ЗАДАЧА УЖЕ ВЫПОЛНЯЕТСЯ.' : 'ЗАДАЧА ЗАПУЩЕНА.', Boolean(data?.meta?.already_running));
        await refreshAgentTasksModalState();
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function handleAgentScheduledTasksListClick(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const actionButton = target.closest('[data-agent-scheduled-action]');
      if (actionButton instanceof HTMLElement) {
        const taskId = String(actionButton.dataset.taskId || '').trim();
        const action = String(actionButton.dataset.agentScheduledAction || '').trim();
        if (!taskId || !action) return;
        try {
          if (action === 'delete') {
            await api('/api/delete_agent_scheduled_task', { method: 'POST', body: { task_id: taskId } });
            if (state.agentScheduledActiveId === taskId) resetAgentScheduledTaskForm();
            setStatus('ЗАДАЧА УДАЛЕНА.', false);
          } else if (action === 'run') {
            const data = await api('/api/run_agent_scheduled_task', { method: 'POST', body: { task_id: taskId } });
            setStatus(data?.meta?.already_running ? 'ЗАДАЧА УЖЕ ВЫПОЛНЯЕТСЯ.' : 'ЗАДАЧА ЗАПУЩЕНА.', Boolean(data?.meta?.already_running));
          } else {
            await api('/api/' + (action === 'pause' ? 'pause' : 'resume') + '_agent_scheduled_task', { method: 'POST', body: { task_id: taskId } });
            setStatus(action === 'pause' ? 'ЗАДАЧА НА ПАУЗЕ.' : 'ЗАДАЧА АКТИВНА.', false);
          }
          await refreshAgentTasksModalState();
        } catch (error) {
          setStatus(error.message, true);
        }
        return;
      }
      const row = target.closest('[data-agent-scheduled-task-id]');
      if (!(row instanceof HTMLElement)) return;
      const taskId = String(row.dataset.agentScheduledTaskId || '').trim();
      const task = (state.agentScheduledTasks || []).find((item) => String(item?.id || '') === taskId);
      if (task) applyAgentScheduledTaskToForm(task);
    }

    function renderAgentRuns(runs) {
      if (!els.agentRunsList) return;
      const items = Array.isArray(runs) ? runs : [];
      const context = activeAiTaskContext();
      const cardId = String(context?.card_id || '').trim();
      const filtered = cardId
        ? items.filter((item) => String(item?.metadata?.context?.card_id || '').trim() === cardId)
        : items;
      const visible = (filtered.length ? filtered : items).slice(0, 2);
      if (!visible.length) {
        els.agentRunsList.innerHTML = '<div class="cashboxes-empty">Запусков пока нет.</div>';
        if (els.agentRunsDetails) els.agentRunsDetails.open = false;
        return;
      }
      if (els.agentRunsDetails) els.agentRunsDetails.open = false;
      els.agentRunsList.innerHTML = visible.map((item) => {
        const taskId = String(item?.task_id || '').trim();
        const status = String(item?.status || '').trim().toLowerCase();
        const statusLabel = status === 'completed' ? 'ГОТОВО' : (status === 'failed' ? 'ОШИБКА' : (status === 'running' ? 'В РАБОТЕ' : 'ОЖИДАЕТ'));
        const summarySource = status === 'failed'
          ? String(item?.error || item?.summary || item?.task_text || 'Запуск агента').trim()
          : String(item?.summary || item?.task_text || 'Запуск агента').trim();
        const summary = status === 'failed'
          ? formatAgentErrorMessage(summarySource)
          : summarizeAgentText(summarySource, 96);
        const meta = [formatDate(item?.finished_at || item?.started_at || ''), String(item?.model || '').trim()].filter(Boolean).join(' · ');
        return '<button class="agent-run-row" type="button" data-agent-task-id="' + escapeHtml(taskId) + '" data-active="' + String(taskId && taskId === state.agentTaskId) + '">'
          + '<div class="agent-run-row__top"><span class="agent-run-row__status">' + escapeHtml(statusLabel) + '</span><span>' + escapeHtml(taskId || 'RUN') + '</span></div>'
          + '<div class="agent-run-row__summary">' + escapeHtml(summary) + '</div>'
          + '<div class="agent-run-row__meta">' + escapeHtml(meta || 'Без времени') + '</div>'
          + '</button>';
      }).join('');
    }

    function renderAgentTask(task) {
      if (!els.agentResultPanel) return;
      if (!task) {
        els.agentResultPanel.dataset.state = 'empty';
        els.agentResultPanel.textContent = 'Введите запрос.';
        delete els.agentResultPanel.dataset.tone;
        renderAgentActions([]);
        if (els.agentDetails) els.agentDetails.open = false;
        return;
      }
      const status = String(task.status || '').trim().toLowerCase();
      if (status === 'running' || status === 'pending') {
        els.agentResultPanel.dataset.state = 'active';
        els.agentResultPanel.textContent = 'Задача принята и выполняется.';
        delete els.agentResultPanel.dataset.tone;
        renderAgentActions([]);
        if (els.agentDetails) els.agentDetails.open = false;
        return;
      }
      if (status === 'failed') {
        els.agentResultPanel.dataset.state = 'error';
        els.agentResultPanel.dataset.tone = 'error';
        els.agentResultPanel.innerHTML = '<div class="agent-result__fallback">' + escapeHtml(formatAgentErrorMessage(task.error || 'Агент завершил задачу с ошибкой.')) + '</div>';
        renderAgentActions([]);
        if (els.agentDetails) els.agentDetails.open = false;
        return;
      }
      const summary = String(task.summary || '').trim();
      const result = String(task.result || '').trim();
      const display = normalizeAgentDisplay(task);
      els.agentResultPanel.dataset.state = summary || result ? 'filled' : 'empty';
      els.agentResultPanel.dataset.tone = display.tone || 'success';
      els.agentResultPanel.innerHTML = (display.title || display.summary || display.sections.length || display.actions.length)
        ? renderAgentDisplay(display)
        : '<div class="agent-result__fallback">' + escapeHtml([summary, result].filter(Boolean).join('\\n\\n') || 'Ответ агента пока пуст.') + '</div>';
    }

    function currentCardAutofillTask(tasks) {
      const items = Array.isArray(tasks) ? tasks : [];
      const card = currentAgentContextCard();
      const cardId = String(card?.id || '').trim();
      if (!cardId) return null;
      return items.find((item) => {
        const metadata = item?.metadata && typeof item.metadata === 'object' ? item.metadata : {};
        const context = metadata.context && typeof metadata.context === 'object' ? metadata.context : {};
        const status = String(item?.status || '').trim().toLowerCase();
        return String(metadata.purpose || '').trim().toLowerCase() === 'card_autofill'
          && String(context.card_id || '').trim() === cardId
          && !isFullCardEnrichmentTask(item)
          && (status === 'pending' || status === 'running');
      }) || null;
    }

    function relevantAgentConsoleTasks(tasks) {
      const items = Array.isArray(tasks) ? tasks : [];
      const context = activeAiTaskContext();
      const kind = String(context.kind || 'board').trim().toLowerCase();
      const cardId = String(context.card_id || '').trim();
      return items.filter((item) => {
        const metadata = item?.metadata && typeof item.metadata === 'object' ? item.metadata : {};
        const taskContext = metadata.context && typeof metadata.context === 'object' ? metadata.context : {};
        if (kind === 'card') return String(taskContext.card_id || '').trim() === cardId;
        return String(taskContext.kind || 'board').trim().toLowerCase() === 'board';
      }).slice(0, 12);
    }

    function summarizeAgentConsoleText(value, limit = 220) {
      const text = String(value || '').replace(/\\s+/g, ' ').trim();
      if (!text) return '';
      return text.length > limit ? (text.slice(0, limit - 1).trim() + '…') : text;
    }

    function mapAgentActionToConsoleEntry(action) {
      if (!action || typeof action !== 'object') return null;
      const kind = String(action.kind || '').trim().toLowerCase();
      const timestamp = String(action.finished_at || action.started_at || '').trim();
      if (kind === 'log') {
        return {
          timestamp,
          level: String(action.level || 'INFO').trim().toUpperCase(),
          message: summarizeAgentConsoleText(action.message || action.result_preview || 'Событие агента'),
          detail: '',
          taskId: String(action.task_id || '').trim(),
          actions: [],
        };
      }
      const tool = String(action.tool || '').trim().toLowerCase();
      const messageMap = {
        get_card: 'Начат анализ карточки.',
        get_card_context: 'Начат анализ карточки.',
        decode_vin: 'Обнаружен VIN. Идёт расшифровка.',
        find_part_numbers: 'Идёт поиск каталожных номеров.',
        search_part_numbers: 'Идёт поиск каталожных номеров.',
        estimate_price_ru: 'Идёт оценка цен по РФ.',
        lookup_part_prices: 'Идёт оценка цен по РФ.',
        estimate_maintenance: 'Найден контекст по ТО.',
        decode_dtc: 'Идёт расшифровка кодов ошибок.',
        search_fault_info: 'Идёт поиск во внешних источниках.',
        search_web: 'Идёт поиск во внешних источниках.',
        fetch_page_excerpt: 'Идёт поиск во внешних источниках.',
        update_card: 'Карточка обновлена.',
        update_repair_order: 'Заказ-наряд обновлён.',
        replace_repair_order_works: 'Заказ-наряд обновлён.',
        replace_repair_order_materials: 'Заказ-наряд обновлён.',
      };
      return {
        timestamp,
        level: tool === 'update_card' ? 'DONE' : 'INFO',
        message: messageMap[tool] || ('Выполнен tool: ' + String(action.tool || 'tool').trim()),
        detail: summarizeAgentConsoleText(action.reason || action.result_preview || '', 260),
        taskId: String(action.task_id || '').trim(),
        actions: [],
      };
    }

    function buildAgentTaskFallbackEntry(task) {
      if (!task || typeof task !== 'object') return null;
      const status = String(task.status || '').trim().toLowerCase();
      let level = 'INFO';
      let message = '';
      if (status === 'running') {
        level = 'RUN';
        message = 'Агент обрабатывает задачу.';
      } else if (status === 'pending') {
        level = 'WAIT';
        message = 'Задача ожидает выполнения.';
      } else if (status === 'failed') {
        level = 'WARN';
        message = 'Ошибка выполнения задачи.';
      } else if (status === 'completed') {
        level = 'DONE';
        message = summarizeAgentConsoleText(task.summary || task.result || 'Задача завершена.');
      } else {
        return null;
      }
      return {
        timestamp: String(task.finished_at || task.started_at || task.created_at || '').trim(),
        level,
        message,
        detail: summarizeAgentConsoleText(task.result || task.error || '', 260),
        taskId: String(task.id || '').trim(),
        actions: [],
      };
    }

    function buildAgentConsoleEntries(tasks, actions, selectedTask) {
      const entries = [];
      const card = currentAgentContextCard();
      const cardLog = Array.isArray(card?.ai_autofill_log) ? card.ai_autofill_log : [];
      cardLog.forEach((entry) => {
        entries.push({
          timestamp: String(entry?.timestamp || '').trim(),
          level: String(entry?.level || 'INFO').trim().toUpperCase(),
          message: summarizeAgentConsoleText(entry?.message || ''),
          detail: '',
          taskId: String(entry?.task_id || '').trim(),
          actions: [],
        });
      });
      const relevantTasks = relevantAgentConsoleTasks(tasks);
      const relevantTaskIds = new Set(relevantTasks.map((item) => String(item?.id || '').trim()).filter(Boolean));
      if (selectedTask?.id) relevantTaskIds.add(String(selectedTask.id || '').trim());
      (Array.isArray(actions) ? actions : []).forEach((action) => {
        const taskId = String(action?.task_id || '').trim();
        if (taskId && relevantTaskIds.size && !relevantTaskIds.has(taskId)) return;
        const mapped = mapAgentActionToConsoleEntry(action);
        if (mapped) entries.push(mapped);
      });
      relevantTasks.forEach((task) => {
        const fallback = buildAgentTaskFallbackEntry(task);
        if (fallback) entries.push(fallback);
      });
      if (selectedTask && typeof selectedTask === 'object') {
        const display = normalizeAgentDisplay(selectedTask);
        const actionItems = Array.isArray(display.actions) ? display.actions.filter(Boolean).slice(0, 4) : [];
        if (display.title || display.summary || actionItems.length) {
          entries.push({
            timestamp: String(selectedTask.finished_at || selectedTask.started_at || selectedTask.created_at || '').trim(),
            level: String(selectedTask.status || '').trim().toLowerCase() === 'failed' ? 'WARN' : 'DONE',
            message: summarizeAgentConsoleText(display.title || selectedTask.summary || selectedTask.result || 'Ответ агента.'),
            detail: summarizeAgentConsoleText(display.summary || selectedTask.result || '', 260),
            taskId: String(selectedTask.id || '').trim(),
            actions: actionItems,
          });
        }
      }
      const seen = new Set();
      return entries
        .filter((item) => item && item.message)
        .sort((left, right) => {
          const leftRaw = new Date(left.timestamp || 0).getTime();
          const rightRaw = new Date(right.timestamp || 0).getTime();
          const leftTime = Number.isFinite(leftRaw) ? leftRaw : 0;
          const rightTime = Number.isFinite(rightRaw) ? rightRaw : 0;
          return leftTime - rightTime;
        })
        .filter((item) => {
          const key = [item.timestamp, item.level, item.message, item.taskId].join('|');
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        })
        .slice(-40);
    }

    function renderAgentConsole(entries) {
      if (!els.agentResultPanel) return;
      const items = Array.isArray(entries) ? entries : [];
      if (!items.length) {
        els.agentResultPanel.dataset.state = 'empty';
        delete els.agentResultPanel.dataset.tone;
        els.agentResultPanel.innerHTML = '<div class="agent-result__fallback">Лента агента пуста. Запусти задачу или включи автосопровождение.</div>';
        return;
      }
      const panel = els.agentResultPanel;
      const distanceToBottom = panel.scrollHeight - panel.scrollTop - panel.clientHeight;
      const shouldStickBottom = distanceToBottom < 48;
      panel.dataset.state = 'filled';
      delete panel.dataset.tone;
      panel.innerHTML = '<div class="agent-console">' + items.map((item) => {
        const timestamp = formatAgentClock(item.timestamp, { withSeconds: true }) || '---';
        const detailHtml = item.detail ? '<div class="agent-console__detail">' + escapeHtml(item.detail) + '</div>' : '';
        const actionsHtml = Array.isArray(item.actions) && item.actions.length
          ? '<div class="agent-console__actions">' + item.actions.map((action) =>
              '<button class="agent-console__action" type="button" data-agent-follow-up="' + escapeHtml(action) + '">' + escapeHtml(action) + '</button>'
            ).join('') + '</div>'
          : '';
        return '<div class="agent-console__entry">'
          + '<div class="agent-console__top"><div class="agent-console__meta"><span class="agent-console__level" data-level="' + escapeHtml(item.level || 'INFO') + '">' + escapeHtml(item.level || 'INFO') + '</span></div><span class="agent-console__timestamp">' + escapeHtml(timestamp) + '</span></div>'
          + '<div class="agent-console__message">' + escapeHtml(item.message || '') + '</div>'
          + detailHtml
          + actionsHtml
          + '</div>';
      }).join('') + '</div>';
      if (shouldStickBottom) panel.scrollTop = panel.scrollHeight;
    }

    function handleAgentResultActionClick(event) {
      const button = event.target.closest('[data-agent-follow-up]');
      if (!button || !els.agentTaskInput) return;
      const followUp = String(button.dataset.agentFollowUp || '').trim();
      if (!followUp) return;
      els.agentTaskInput.value = followUp;
      syncAgentTaskInputHeight();
      els.agentTaskInput.focus();
    }

    function selectAgentTask(tasks) {
      const items = Array.isArray(tasks) ? tasks : [];
      if (!items.length) return null;
      if (state.agentTaskId) {
        const exact = items.find((item) => item?.id === state.agentTaskId);
        if (exact) return exact;
      }
      const context = activeAiTaskContext();
      const contextKind = String(context.kind || 'board').trim().toLowerCase();
      const contextCardId = String(context.card_id || '').trim();
      const matchesContext = (item) => {
        const metadataContext = item?.metadata?.context && typeof item.metadata.context === 'object'
          ? item.metadata.context
          : {};
        const itemKind = String(metadataContext.kind || 'board').trim().toLowerCase();
        if (contextKind === 'card') return String(metadataContext.card_id || '').trim() === contextCardId;
        return itemKind === 'board';
      };
      const active = items.find((item) => {
        if (!matchesContext(item)) return false;
        const itemStatus = String(item?.status || '').trim().toLowerCase();
        return itemStatus === 'pending' || itemStatus === 'running';
      });
      return active || null;
    }

    async function syncAgentTaskEffects(task) {
      if (!task || task.status !== 'completed' || state.agentSyncedTaskId === task.id) return;
      state.agentSyncedTaskId = task.id;
      const context = task.metadata && typeof task.metadata === 'object' ? task.metadata.context : null;
      const cardId = String(context?.card_id || '').trim();
      try {
        await refreshSnapshot(false);
      } catch (_) {}
      if (cardId && state.editingId === cardId && els.cardModal?.classList.contains('is-open')) {
        try {
          const data = await api('/api/get_card?card_id=' + encodeURIComponent(cardId));
          if (data?.card) applyCardModalState(data.card);
        } catch (_) {}
      }
    }

    function isAnyAgentSurfaceOpen() {
      return Boolean(els.agentModal?.classList.contains('is-open') || els.aiSurfaceModal?.classList.contains('is-open') || els.aiChatWindow?.classList.contains('is-open'));
    }

    function scheduleAgentRefresh(delay = 3000) {
      if (state.agentRefreshTimer) window.clearTimeout(state.agentRefreshTimer);
      if (!isAnyAgentSurfaceOpen()) return;
      state.agentRefreshTimer = window.setTimeout(refreshAgentModalState, delay);
    }

    async function refreshAgentModalState() {
      if (!isAnyAgentSurfaceOpen()) return;
      try {
        const card = currentAgentContextCard();
        const cardId = String(card?.id || '').trim();
        const requests = [
          api('/api/agent_status'),
          api('/api/agent_tasks?limit=20'),
          api('/api/agent_actions?limit=80'),
        ];
        if (cardId) requests.push(api('/api/get_card?card_id=' + encodeURIComponent(cardId)));
        const [statusData, tasksData, actionsData, cardData] = await Promise.all(requests);
        state.agentStatusPayload = statusData || null;
        state.agentLatestTasks = Array.isArray(tasksData?.tasks) ? tasksData.tasks : [];
        state.agentLatestActions = Array.isArray(actionsData?.actions) ? actionsData.actions : [];
        if (cardData?.card) {
          state.activeCard = cardData.card;
          if (els.cardModal?.classList.contains('is-open')) applyCardModalState(cardData.card);
        }
        renderAgentStatus(statusData);
        renderAiEntrySurface(statusData);
        renderAiChatWindow(statusData);
        syncBoardControlSettingsForm();
        renderAgentRuns(statusData?.recent_runs || []);
        const task = selectAgentTask(state.agentLatestTasks);
        if (task) {
          state.agentTaskId = String(task.id || state.agentTaskId || '');
          state.agentTaskStatus = String(task.status || '');
        }
        renderAgentConsole(buildAgentConsoleEntries(state.agentLatestTasks, state.agentLatestActions, task));
        if (task?.id) {
          const taskActionsData = await api('/api/agent_actions?limit=25&task_id=' + encodeURIComponent(task.id));
          renderAgentActions(taskActionsData?.actions || []);
          await syncAgentTaskEffects(task);
        } else {
          renderAgentActions([]);
        }
        const hasRunningAutofill = Boolean(currentCardAutofillTask(state.agentLatestTasks));
        const activeAutofill = Boolean(currentAgentContextCard()?.ai_autofill_active);
        scheduleAgentRefresh(task && (task.status === 'pending' || task.status === 'running')
          ? 1200
          : ((hasRunningAutofill || activeAutofill) ? 1800 : 3500));
      } catch (error) {
        renderAgentStatus({ agent: { enabled: false }, status: { running: false, last_error: error.message }, queue: { pending_total: 0 } });
        syncBoardControlSettingsForm();
        if (els.agentResultPanel) {
          els.agentResultPanel.dataset.state = 'error';
          els.agentResultPanel.dataset.tone = 'error';
          els.agentResultPanel.innerHTML = '<div class="agent-result__fallback">' + escapeHtml(formatAgentErrorMessage(error.message)) + '</div>';
        }
        scheduleAgentRefresh(5000);
      }
    }

    function openAgentModal(kind = 'board') {
      if (!requireOperatorSession()) return;
      ensureAgentUi();
      bindAgentUiEvents();
      state.agentContext = buildAgentContext(kind);
      state.agentTaskId = '';
      state.agentTaskStatus = '';
      state.agentAutofillPromptOpen = false;
      if (els.agentContextLabel) els.agentContextLabel.textContent = formatAgentContextLabel(state.agentContext);
      renderAgentQuickActions(state.agentContext);
      if (els.agentTaskInput) {
        els.agentTaskInput.placeholder = agentPlaceholder(state.agentContext);
        delete els.agentTaskInput.dataset.agentPromptTemplate;
        if (!String(els.agentTaskInput.value || '').trim()) els.agentTaskInput.value = '';
      }
      if (els.agentResultPanel) {
        els.agentResultPanel.dataset.state = 'empty';
        delete els.agentResultPanel.dataset.tone;
        els.agentResultPanel.innerHTML = '<div class="agent-result__fallback">Лента агента пуста. Запусти задачу или включи автосопровождение.</div>';
      }
      state.agentStatusPayload = null;
      state.agentLatestTasks = [];
      state.agentLatestActions = [];
      renderAgentAutofillControls({ agent: { enabled: false } });
      renderAgentActions([]);
      renderAgentRuns([]);
      if (els.agentRunsDetails) els.agentRunsDetails.open = false;
      if (els.agentDetails) els.agentDetails.open = false;
      els.agentModal.classList.add('is-open');
      if (state.agentAutofillCountdownTimer) window.clearInterval(state.agentAutofillCountdownTimer);
      state.agentAutofillCountdownTimer = window.setInterval(() => {
        renderAgentAutofillControls(state.agentStatusPayload || { agent: { enabled: false } });
      }, 1000);
      refreshAgentModalState();
      window.setTimeout(() => {
        syncAgentTaskInputHeight();
        els.agentTaskInput?.focus();
      }, 0);
    }

    function closeAgentModal() {
      if (!(els.agentModal instanceof HTMLElement)) {
        hydrateAgentUiRefs();
      }
      els.agentModal?.classList.remove('is-open');
      state.agentAutofillPromptOpen = false;
      if (!isAnyAgentSurfaceOpen() && state.agentRefreshTimer) {
        window.clearTimeout(state.agentRefreshTimer);
        state.agentRefreshTimer = null;
      }
      if (state.agentAutofillCountdownTimer) {
        window.clearInterval(state.agentAutofillCountdownTimer);
        state.agentAutofillCountdownTimer = null;
      }
    }

    async function enqueueAgentTask() {
      if (!requireOperatorSession()) return;
      const taskText = String(els.agentTaskInput?.value || '').trim();
      if (!taskText) {
        setStatus('Введите задачу для агента.', true);
        els.agentTaskInput?.focus();
        return;
      }
      const context = buildAgentContext(state.agentContext?.kind || 'board');
      state.agentContext = context;
      if (els.agentContextLabel) els.agentContextLabel.textContent = formatAgentContextLabel(context);
      try {
        const quickTemplate = String(els.agentTaskInput?.dataset.agentPromptTemplate || '').trim();
        const data = await api('/api/agent_enqueue_task', {
          method: 'POST',
          body: {
            source: 'ui_agent',
            mode: 'manual',
            task_text: taskText,
            metadata: {
              context,
              quick_template: quickTemplate,
            },
          },
        });
        state.agentTaskId = String(data?.task?.id || '');
        state.agentTaskStatus = String(data?.task?.status || '');
        if (els.agentResultPanel) {
          els.agentResultPanel.dataset.state = 'active';
          delete els.agentResultPanel.dataset.tone;
          renderAgentConsole([
            {
              timestamp: new Date().toISOString(),
              level: 'RUN',
              message: 'Задача принята и выполняется.',
              detail: summarizeAgentConsoleText(taskText, 220),
              taskId: state.agentTaskId,
              actions: [],
            },
          ]);
        }
        if (els.agentDetails) els.agentDetails.open = false;
        refreshAgentModalState();
      } catch (error) {
        if (els.agentResultPanel) {
          els.agentResultPanel.dataset.state = 'error';
          els.agentResultPanel.dataset.tone = 'error';
          els.agentResultPanel.innerHTML = '<div class="agent-result__fallback">' + escapeHtml(formatAgentErrorMessage(error.message)) + '</div>';
        }
        setStatus(error.message, true);
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
      try {
        const date = value instanceof Date ? value : new Date(value);
        if (Number.isNaN(date.getTime())) return value;
        const dd = String(date.getDate()).padStart(2, '0');
        const mm = String(date.getMonth() + 1).padStart(2, '0');
        const yy = String(date.getFullYear() % 100).padStart(2, '0');
        const hh = String(date.getHours()).padStart(2, '0');
        const min = String(date.getMinutes()).padStart(2, '0');
        return dd + '.' + mm + '.' + yy + ', ' + hh + ':' + min;
      } catch { return value; }
    }

    function formatAgentClock(value, { withSeconds = false } = {}) {
      if (!value) return '';
      try {
        const date = value instanceof Date ? value : new Date(value);
        if (Number.isNaN(date.getTime())) return '';
        return date.toLocaleTimeString('ru-RU', withSeconds
          ? { hour: '2-digit', minute: '2-digit', second: '2-digit' }
          : { hour: '2-digit', minute: '2-digit' });
      } catch {
        return '';
      }
    }

    function formatAgentCountdown(untilText) {
      if (!untilText) return '';
      try {
        const untilAt = new Date(untilText);
        const diffMs = untilAt.getTime() - Date.now();
        if (!Number.isFinite(diffMs) || diffMs <= 0) return '00:00';
        const totalMinutes = Math.ceil(diffMs / 60000);
        const hours = Math.max(0, Math.floor(totalMinutes / 60));
        const minutes = Math.max(0, totalMinutes % 60);
        return String(hours).padStart(2, '0') + ':' + String(minutes).padStart(2, '0');
      } catch {
        return '';
      }
    }

    function boardScaleStorageKey(actor = state.actor) {
      const normalizedActor = String(actor || '').trim().toUpperCase();
      return BOARD_SCALE_STORAGE_KEY_PREFIX + (normalizedActor || 'GUEST');
    }

    function readStoredBoardScale(actor = state.actor) {
      try {
        const rawValue = localStorage.getItem(boardScaleStorageKey(actor));
        if (!rawValue) return null;
        const numeric = Number(rawValue);
        return Number.isFinite(numeric) ? normalizeBoardScale(numeric) : null;
      } catch (_) {
        return null;
      }
    }

    function persistStoredBoardScale(value, actor = state.actor) {
      const scale = normalizeBoardScale(value);
      try {
        localStorage.setItem(boardScaleStorageKey(actor), String(scale));
      } catch (_) {
      }
      return scale;
    }

    function resolveBoardScalePreference(fallbackValue = 1, { persistFallback = false } = {}) {
      const storedScale = readStoredBoardScale();
      if (storedScale !== null) return storedScale;
      const fallbackScale = normalizeBoardScale(fallbackValue);
      if (persistFallback) persistStoredBoardScale(fallbackScale);
      return fallbackScale;
    }

    function applyBoardScalePreference({ fallbackValue = 1, syncInput = false, persistFallback = false } = {}) {
      return applyBoardScale(resolveBoardScalePreference(fallbackValue, { persistFallback }), { syncInput });
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
      if (!els.statusLine || els.statusLine.parentElement !== els.boardScroll) return;
      if (els.topbarStatusHost) {
        els.topbarStatusHost.appendChild(els.statusLine);
        return;
      }
      if (!els.boardScroll) return;
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

    function clampSignalPart(kind, value) {
      const limit = kind === 'days' ? 365 : 23;
      return Math.max(0, Math.min(limit, value));
    }

    function setSignalPartValue(kind, value) {
      const input = kind === 'days' ? els.signalDays : els.signalHours;
      if (!input) return;
      const nextValue = clampSignalPart(kind, Number(value || 0));
      input.value = String(nextValue);
    }

    function signalPartValue(kind) {
      const input = kind === 'days' ? els.signalDays : els.signalHours;
      return clampSignalPart(kind, Number(input?.value || 0));
    }

    function adjustSignalPart(kind, delta) {
      setSignalPartValue(kind, signalPartValue(kind) + Number(delta || 0));
      renderSignalPreview();
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
      if (vehicle && title && title.toLowerCase().includes(vehicle.toLowerCase())) return title;
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

    function syncAgentTaskInputHeight() {
      const textarea = els.agentTaskInput;
      if (!textarea) return;
      const style = window.getComputedStyle(textarea);
      const lineHeight = Math.max(20, parseFloat(style.lineHeight || '22'));
      const paddingTop = parseFloat(style.paddingTop || '0');
      const paddingBottom = parseFloat(style.paddingBottom || '0');
      const borderTop = parseFloat(style.borderTopWidth || '0');
      const borderBottom = parseFloat(style.borderBottomWidth || '0');
      const chromeHeight = paddingTop + paddingBottom + borderTop + borderBottom;
      const text = String(textarea.value || '').trim();
      const lineCount = text ? text.split(/\\r?\\n/).length : 0;
      const minRows = text ? Math.max(3, Math.min(6, lineCount + 1)) : 3;
      const minHeight = Math.round(minRows * lineHeight + chromeHeight);
      const maxHeight = Math.max(minHeight, Math.min(window.innerHeight * 0.24, 180));
      textarea.style.height = 'auto';
      textarea.style.height = Math.max(minHeight, Math.min(textarea.scrollHeight, maxHeight)) + 'px';
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
      const lineCount = text ? text.split(/\\r?\\n/).length : 0;
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
        '<div class="sticky__head"><span class="sticky__pin">СТИКЕР</span><button class="sticky__close" type="button" data-delete-sticky="' + escapeHtml(sticky.id) + '" title="Удалить">?</button></div>' +
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
      if (els.signalDaysDisplay) els.signalDaysDisplay.textContent = String(signalPartValue('days')).padStart(2, '0');
      if (els.signalHoursDisplay) els.signalHoursDisplay.textContent = String(signalPartValue('hours')).padStart(2, '0');
      if (els.signalDaysDecrementButton) els.signalDaysDecrementButton.disabled = signalPartValue('days') <= 0;
      if (els.signalDaysIncrementButton) els.signalDaysIncrementButton.disabled = signalPartValue('days') >= 365;
      if (els.signalHoursDecrementButton) els.signalHoursDecrementButton.disabled = signalPartValue('hours') <= 0;
      if (els.signalHoursIncrementButton) els.signalHoursIncrementButton.disabled = signalPartValue('hours') >= 23;
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
            + '<button class="btn btn--ghost repair-order-tag-remove" type="button" data-remove-repair-order-tag="' + escapeHtml(tag.label) + '" aria-label="Удалить метку">?</button>'
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

    const ATTACHMENT_ALLOWED_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.webp', '.gif', '.doc', '.docx', '.xls', '.xlsx', '.txt', '.pdf']);
    const ATTACHMENT_ALLOWED_LABEL = 'PNG, JPG, JPEG, WEBP, GIF, DOC, DOCX, XLS, XLSX, TXT, PDF';
    const ATTACHMENT_MAX_SIZE_BYTES = 15 * 1024 * 1024;
    const ATTACHMENT_EXTENSION_TO_MIME = {
      '.png': 'image/png',
      '.jpg': 'image/jpeg',
      '.jpeg': 'image/jpeg',
      '.webp': 'image/webp',
      '.gif': 'image/gif',
      '.doc': 'application/msword',
      '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      '.xls': 'application/vnd.ms-excel',
      '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      '.txt': 'text/plain',
      '.pdf': 'application/pdf',
    };
    const ATTACHMENT_EXTENSION_TO_ALLOWED_MIMES = {
      '.png': ['image/png'],
      '.jpg': ['image/jpeg', 'image/jpg', 'image/pjpeg'],
      '.jpeg': ['image/jpeg', 'image/jpg', 'image/pjpeg'],
      '.webp': ['image/webp'],
      '.gif': ['image/gif'],
      '.doc': ['application/msword'],
      '.docx': ['application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/zip'],
      '.xls': ['application/vnd.ms-excel'],
      '.xlsx': ['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'application/zip'],
      '.txt': ['text/plain'],
      '.pdf': ['application/pdf'],
    };
    const ATTACHMENT_MIME_TO_EXTENSION = {
      'image/png': '.png',
      'image/jpeg': '.jpg',
      'image/jpg': '.jpg',
      'image/pjpeg': '.jpg',
      'image/webp': '.webp',
      'image/gif': '.gif',
      'application/msword': '.doc',
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
      'application/vnd.ms-excel': '.xls',
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
      'text/plain': '.txt',
      'application/pdf': '.pdf',
    };

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

    function attachmentStamp() {
      const stamp = new Date().toISOString().replace(/[.]\\d+Z$/, 'Z').replace(/[:T]/g, '-');
      return stamp;
    }

    function clipboardAttachmentName(prefix, extension) {
      return prefix + '-' + attachmentStamp() + extension;
    }

    function clipboardTextAttachmentName() {
      return clipboardAttachmentName('clipboard', '.txt');
    }

    function normalizeAttachmentMimeType(mimeType) {
      return String(mimeType || '').split(';', 1)[0].trim().toLowerCase();
    }

    function attachmentExtension(fileName) {
      const normalizedName = String(fileName || '').trim();
      const dotIndex = normalizedName.lastIndexOf('.');
      if (dotIndex < 0) return '';
      return normalizedName.slice(dotIndex).toLowerCase();
    }

    function attachmentMimeTypeFromExtension(extension) {
      return ATTACHMENT_EXTENSION_TO_MIME[String(extension || '').toLowerCase()] || '';
    }

    function attachmentValidationMessage() {
      return 'РАЗРЕШЕНЫ ТОЛЬКО ' + ATTACHMENT_ALLOWED_LABEL + '.';
    }

    function normalizeUploadableAttachmentFile(file) {
      if (!(file instanceof File)) {
        throw new Error('НЕ УДАЛОСЬ ПРОЧИТАТЬ ФАЙЛ ДЛЯ ЗАГРУЗКИ.');
      }
      const normalizedMime = normalizeAttachmentMimeType(file.type);
      let fileName = String(file.name || '').trim();
      let extension = attachmentExtension(fileName);
      if (!fileName && ATTACHMENT_MIME_TO_EXTENSION[normalizedMime]) {
        const prefix = normalizedMime.startsWith('image/') ? 'clipboard-image' : 'attachment';
        fileName = clipboardAttachmentName(prefix, ATTACHMENT_MIME_TO_EXTENSION[normalizedMime]);
        extension = attachmentExtension(fileName);
      }
      if (!fileName) {
        throw new Error('НЕ УДАЛОСЬ ОПРЕДЕЛИТЬ ИМЯ ФАЙЛА ДЛЯ ЗАГРУЗКИ.');
      }
      if (!extension && ATTACHMENT_MIME_TO_EXTENSION[normalizedMime]) {
        fileName += ATTACHMENT_MIME_TO_EXTENSION[normalizedMime];
        extension = attachmentExtension(fileName);
      }
      if (!extension || !ATTACHMENT_ALLOWED_EXTENSIONS.has(extension)) {
        throw new Error(attachmentValidationMessage());
      }
      if (file.size > ATTACHMENT_MAX_SIZE_BYTES) {
        throw new Error('ФАЙЛ СЛИШКОМ БОЛЬШОЙ. ЛИМИТ 15 МБ.');
      }
      const allowedMimeTypes = ATTACHMENT_EXTENSION_TO_ALLOWED_MIMES[extension] || [];
      if (normalizedMime && normalizedMime !== 'application/octet-stream' && !allowedMimeTypes.includes(normalizedMime)) {
        throw new Error('MIME-ТИП ФАЙЛА НЕ СООТВЕТСТВУЕТ ЕГО РАСШИРЕНИЮ.');
      }
      const finalMimeType = normalizedMime || attachmentMimeTypeFromExtension(extension) || 'application/octet-stream';
      if (fileName === file.name && finalMimeType === (file.type || '')) {
        return file;
      }
      return new File([file], fileName, { type: finalMimeType, lastModified: file.lastModified || Date.now() });
    }

    function collectClipboardAttachmentFiles(event) {
      const files = [];
      const items = Array.from(event.clipboardData?.items || []);
      items.forEach((item) => {
        if (item.kind !== 'file') return;
        const file = item.getAsFile();
        if (!file) return;
        const normalizedMime = normalizeAttachmentMimeType(file.type);
        if (!String(file.name || '').trim() && ATTACHMENT_MIME_TO_EXTENSION[normalizedMime]) {
          const prefix = normalizedMime.startsWith('image/') ? 'clipboard-image' : 'attachment';
          const generatedName = clipboardAttachmentName(prefix, ATTACHMENT_MIME_TO_EXTENSION[normalizedMime]);
          files.push(new File([file], generatedName, { type: file.type || attachmentMimeTypeFromExtension(ATTACHMENT_MIME_TO_EXTENSION[normalizedMime]), lastModified: Date.now() }));
          return;
        }
        files.push(file);
      });
      if (files.length) return files;
      const text = event.clipboardData?.getData('text/plain') || '';
      if (!text.trim()) return [];
      return [new File([text], clipboardTextAttachmentName(), { type: 'text/plain' })];
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
        .split(/[\\n,]/)
        .map((item) => item.trim())
        .filter(Boolean);
    }

    function vehicleDisplayFromProfile(profile) {
      const displayName = String(profile?.display_name || '').trim();
      if (displayName) return profile?.production_year ? (displayName + ' ' + profile.production_year) : displayName;
      const parts = [profile?.make_display, profile?.model_display].filter(Boolean);
      if (!parts.length) return '';
      const base = parts.join(' ');
      return profile?.production_year ? (base + ' ' + profile.production_year) : base;
    }

    function vehicleCompletionLabel(value) {
      return VEHICLE_COMPLETION_LABELS[String(value || '').trim()] || 'данные уточняются';
    }

    function vinLooksSuspicious(value) {
      const normalized = String(value || '').toUpperCase().replace(/\\s+/g, '');
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
      els.vehicleProfileFields.innerHTML = VEHICLE_FIELD_GROUPS.map((group, index) => {
        const fields = group.fields.map((field) => {
          const copyButton = field.copy
            ? '<button class="vehicle-copy" type="button" data-copy-vehicle-field="' + escapeHtml(field.name) + '">копия</button>'
            : '';
          return '<div class="field field--compact vehicle-field' + (field.wide ? ' vehicle-field--wide' : '') + '">' +
            '<div class="vehicle-field__label"><span>' + escapeHtml(field.label) + '</span>' + copyButton + '</div>' +
            vehicleFieldControlHtml(field) +
            '</div>';
        }).join('');
        return '<section class="vehicle-group' + (index === 0 ? ' vehicle-group--identity' : '') + '">' +
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
      return '<article class="card" draggable="true" data-card-id="' + escapeHtml(card.id) + '" data-indicator="' + escapeHtml(card.indicator) + '" data-status="' + escapeHtml(card.status) + '" data-blink="' + (card.is_blinking ? 'true' : 'false') + '">' + headingHtml + '<div class="card__desc">' + escapeHtml(card.description || 'Описание не указано') + '</div><div class="card__signal"><span class="card__signal-label"><span class="lamp" data-indicator="' + escapeHtml(card.indicator) + '"></span><span>СИГН</span></span><span class="card__signal-value">' + durationToMarkup(card.remaining_seconds, false) + '</span></div><div class="card__tags">' + tagsHtml + '</div></article>';
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
      return '<article class="card" style="' + heatStyle + '" draggable="true" data-card-id="' + escapeHtml(card.id) + '" data-indicator="' + escapeHtml(card.indicator) + '" data-status="' + escapeHtml(card.status) + '" data-blink="' + (card.is_blinking ? "true" : "false") + '" data-deadline-bucket="' + escapeHtml(card.deadline_progress_bucket ?? 0) + '" data-deadline-step="' + escapeHtml(card.deadline_progress_step_percent ?? 0) + '">' + headingHtml + '<div class="card__desc">' + escapeHtml(card.description || 'Описание не указано') + '</div><div class="card__signal"><span class="card__signal-label"><span class="lamp" data-indicator="' + escapeHtml(card.indicator) + '"></span><span>СИГН</span></span><span class="card__signal-value">' + durationToMarkup(card.remaining_seconds, false) + '</span></div><div class="card__tags">' + tagsHtml + '</div></article>';
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
      return '<article class="card" style="' + heatStyle + '" draggable="true" data-card-id="' + escapeHtml(card.id) + '" data-indicator="' + escapeHtml(card.indicator) + '" data-status="' + escapeHtml(card.status) + '" data-blink="' + (card.is_blinking ? "true" : "false") + '" data-unread="' + (card.is_unread ? 'true' : 'false') + '" data-deadline-bucket="' + escapeHtml(card.deadline_progress_bucket ?? 0) + '" data-deadline-step="' + escapeHtml(card.deadline_progress_step_percent ?? 0) + '">' + unreadBadgeHtml + headingHtml + '<div class="card__desc">' + escapeHtml(card.description || 'Описание не указано') + '</div><div class="card__signal"><span class="card__signal-label"><span class="lamp" data-indicator="' + escapeHtml(card.indicator) + '"></span><span>СИГН</span></span><span class="card__signal-value">' + durationToMarkup(card.remaining_seconds, false) + '</span></div><div class="card__tags">' + tagsHtml + '</div></article>';
    };

    function legacyRefreshVehiclePanelShadow() {
      const profile = cloneVehicleProfile(state.vehicleProfileDraft || emptyVehicleProfile());
      const summaryLines = [];
      if (profile.vin) summaryLines.push('VIN: ' + profile.vin);
      if (profile.mileage) summaryLines.push('Пробег: ' + profile.mileage);
      els.vehiclePanelSummary.textContent = summaryLines.join('\\n');
      els.vehiclePanelSummary.style.display = summaryLines.length ? '' : 'none';

      const vinInput = getVehicleFieldInput('vin');
      if (vinInput) vinInput.classList.toggle('vehicle-suspect', vinLooksSuspicious(profile.vin));

      if (!state.vehicleAutofillResult) renderVehicleAutofillStatus(defaultVehicleStatusText(profile), Boolean(profile?.warnings?.length || vinLooksSuspicious(profile.vin)));
    }

    function applyVehicleProfileToForm(profile, { preserveStatus = false } = {}) {
      const normalized = cloneVehicleProfile(profile);
      if (!String(normalized.display_name || '').trim()) {
        normalized.display_name = vehicleDisplayFromProfile(normalized);
      }
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
        const temp = document.createElement('textarea');
        temp.value = value;
        temp.setAttribute('readonly', 'readonly');
        temp.style.position = 'fixed';
        temp.style.left = '-1000px';
        temp.style.top = '-1000px';
        temp.style.opacity = '0';
        document.body.appendChild(temp);
        temp.focus();
        temp.select();
        temp.setSelectionRange(0, temp.value.length);
        let copied = false;
        try {
          copied = document.execCommand('copy');
        } catch (_) {
          copied = false;
        }
        temp.remove();
        if (!copied && navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(value);
          copied = true;
        }
        if (!copied) throw new Error('copy_failed');
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
        column: state.activeCard?.column || state.snapshot?.columns?.[0]?.id || '',
        tags: state.draftTags.map((tag) => ({ label: tag.label, color: tag.color })),
        deadline: deadlineInput(),
        vehicle_profile: vehicleProfile,
      };
    }

    function emptyRepairOrderRow() {
      return {
        name: '',
        quantity: '',
        price: '',
        total: '',
        executor_id: '',
        executor_name: '',
        salary_mode_snapshot: '',
        base_salary_snapshot: '',
        work_percent_snapshot: '',
        salary_amount: '',
        salary_accrued_at: '',
      };
    }

    function repairOrderParseNumber(value) {
      const normalized = String(value ?? '')
        .trim()
        .replace(/\\s+/g, '')
        .replace(',', '.')
        .replace(/[^0-9.-]/g, '');
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
      return rounded.toFixed(2).replace(/0+$/, '').replace(/\\.$/, '');
    }

    function repairOrderFormatMoney(value) {
      const normalized = typeof value === 'number' ? value : repairOrderParseNumber(value);
      const safeValue = normalized === null ? 0 : normalized;
      return new Intl.NumberFormat('ru-RU', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }).format(safeValue);
    }

    function normalizeRepairOrderPaymentMethod(value) {
      const normalized = String(value ?? '').trim().toLowerCase();
      if (['cashless', 'wire', 'bank', 'безнал', 'безналичный'].includes(normalized)) return 'cashless';
      return 'cash';
    }

    function repairOrderPaymentMethodFromCashboxName(value, fallback = 'cash') {
      const normalized = String(value ?? '').trim().toLowerCase();
      if (!normalized) return normalizeRepairOrderPaymentMethod(fallback);
      if (normalized.includes('безнал') || normalized.includes('cashless') || normalized.includes('wire') || normalized.includes('bank')) {
        return 'cashless';
      }
      return 'cash';
    }

    function selectedRepairOrderPaymentCashbox() {
      const cashboxId = String(els.repairOrderPaymentCashbox?.value || '').trim();
      if (!cashboxId) return null;
      return (Array.isArray(state.cashboxes) ? state.cashboxes : []).find((item) => String(item?.id || '').trim() === cashboxId) || null;
    }

    function repairOrderPaymentMethodFromPayments(payments, fallback = 'cash') {
      const normalizedPayments = Array.isArray(payments) ? payments : [];
      if (!normalizedPayments.length) return normalizeRepairOrderPaymentMethod(fallback);
      return normalizedPayments.some((item) => {
        return repairOrderPaymentMethodFromCashboxName(
          item?.cashbox_name || '',
          item?.payment_method || fallback
        ) === 'cashless';
      }) ? 'cashless' : 'cash';
    }

    function emptyRepairOrderPayment() {
      return {
        id: '',
        amount: '',
        paid_at: '',
        note: '',
        payment_method: 'cash',
        actor_name: '',
        cashbox_id: '',
        cashbox_name: '',
        cash_transaction_id: '',
      };
    }

    function normalizeRepairOrderPayment(payment, fallbackId = '') {
      const source = payment && typeof payment === 'object' ? payment : {};
      const amountValue = repairOrderParseNumber(source.amount ?? source.value);
      return {
        id: String(source.id ?? fallbackId ?? '').trim() || fallbackId || ('payment-' + Date.now()),
        amount: amountValue === null ? String(source.amount ?? source.value ?? '').trim() : repairOrderNumberToRaw(amountValue),
        paid_at: String(source.paid_at ?? source.paidAt ?? source.date ?? '').trim(),
        note: String(source.note ?? source.comment ?? source.description ?? '').trim(),
        payment_method: repairOrderPaymentMethodFromCashboxName(
          source.cashbox_name ?? source.cashboxName ?? '',
          source.payment_method ?? source.paymentMethod ?? 'cash'
        ),
        actor_name: String(source.actor_name ?? source.actorName ?? '').trim(),
        cashbox_id: String(source.cashbox_id ?? source.cashboxId ?? '').trim(),
        cashbox_name: String(source.cashbox_name ?? source.cashboxName ?? '').trim(),
        cash_transaction_id: String(source.cash_transaction_id ?? source.cashTransactionId ?? '').trim(),
      };
    }

    function normalizeRepairOrderPayments(payments, legacyPrepayment = '', defaultPaidAt = '') {
      const normalizedItems = Array.isArray(payments)
        ? payments.map((item, index) => normalizeRepairOrderPayment(item, 'payment-' + (index + 1))).filter((item) => item.amount || item.note || item.paid_at)
        : [];
      if (normalizedItems.length) return normalizedItems;
      const legacyAmount = repairOrderParseNumber(legacyPrepayment);
      if (legacyAmount === null || legacyAmount === 0) return [];
      return [
        normalizeRepairOrderPayment(
          {
            id: 'legacy-prepayment',
            amount: repairOrderNumberToRaw(legacyAmount),
            paid_at: defaultPaidAt || currentRepairOrderDateTime(),
            note: 'Перенесено из предоплаты',
            payment_method: 'cash',
          },
          'legacy-prepayment'
        ),
      ];
    }

    function repairOrderPaymentsTotalValue(payments) {
      return repairOrderRoundMoney((Array.isArray(payments) ? payments : []).reduce((total, item) => {
        return total + (repairOrderParseNumber(item?.amount) ?? 0);
      }, 0));
    }

    function repairOrderCashlessPaymentsValue(payments) {
      return repairOrderRoundMoney((Array.isArray(payments) ? payments : []).reduce((total, item) => {
        const paymentMethod = repairOrderPaymentMethodFromCashboxName(
          item?.cashbox_name ?? item?.cashboxName ?? '',
          item?.payment_method ?? item?.paymentMethod ?? 'cash'
        );
        if (paymentMethod !== 'cashless') return total;
        return total + (repairOrderParseNumber(item?.amount) ?? 0);
      }, 0));
    }

    function repairOrderPaymentMethodLabel(value) {
      return normalizeRepairOrderPaymentMethod(value) === 'cashless' ? 'Безналичный' : 'Наличный';
    }

    function repairOrderTaxRate(value) {
      return normalizeRepairOrderPaymentMethod(value) === 'cashless' ? 0.15 : 0;
    }

    function syncRepairOrderPaymentMethod(value) {
      const normalized = normalizeRepairOrderPaymentMethod(value);
      if (els.repairOrderPaymentMethod) els.repairOrderPaymentMethod.value = normalized;
      return normalized;
    }

    function syncRepairOrderPaymentMethodFromPayments() {
      return syncRepairOrderPaymentMethod(repairOrderPaymentMethodFromPayments(state.repairOrderPayments, 'cash'));
    }

    function renderRepairOrderPaymentCashboxOptions(selectedId = '') {
      if (!els.repairOrderPaymentCashbox) return;
      const selected = String(selectedId || '').trim();
      const items = Array.isArray(state.cashboxes) ? state.cashboxes : [];
      const options = ['<option value="">ВЫБЕРИ КАССУ</option>'].concat(items.map((item) => {
        const itemId = String(item?.id || '').trim();
        const isSelected = itemId && itemId === selected ? ' selected' : '';
        return '<option value="' + escapeHtml(itemId) + '"' + isSelected + '>' + escapeHtml(item?.name || itemId || 'Касса') + '</option>';
      }));
      els.repairOrderPaymentCashbox.innerHTML = options.join('');
      if (!selected && items.length && !els.repairOrderPaymentCashbox.value) {
        els.repairOrderPaymentCashbox.value = String(items[0]?.id || '').trim();
      }
    }

    async function ensureRepairOrderPaymentCashboxes() {
      if (Array.isArray(state.cashboxes) && state.cashboxes.length) {
        renderRepairOrderPaymentCashboxOptions(els.repairOrderPaymentCashbox?.value || '');
        return;
      }
      const data = await api('/api/list_cashboxes?limit=200');
      state.cashboxes = Array.isArray(data?.cashboxes) ? data.cashboxes : [];
      renderRepairOrderPaymentCashboxOptions(els.repairOrderPaymentCashbox?.value || '');
    }

    function repairOrderRowHasAnyData(row) {
      return ['name', 'catalog_number', 'quantity', 'price', 'total'].some((fieldName) => String(row?.[fieldName] ?? '').trim());
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
        catalog_number: String(source.catalog_number ?? source.part_number ?? source.catalogNumber ?? source.partNumber ?? '').trim(),
        quantity: String(source.quantity ?? '').trim(),
        price: String(source.price ?? '').trim(),
        total: totalValue === null ? fallbackTotal : repairOrderNumberToRaw(totalValue),
        executor_id: String(source.executor_id ?? source.employee_id ?? '').trim(),
        executor_name: String(source.executor_name ?? source.employee_name ?? '').trim(),
        salary_mode_snapshot: String(source.salary_mode_snapshot ?? '').trim(),
        base_salary_snapshot: String(source.base_salary_snapshot ?? '').trim(),
        work_percent_snapshot: String(source.work_percent_snapshot ?? '').trim(),
        salary_amount: String(source.salary_amount ?? '').trim(),
        salary_accrued_at: String(source.salary_accrued_at ?? '').trim(),
      };
    }

    function normalizeRepairOrder(order) {
      const source = order && typeof order === 'object' ? order : {};
      const normalizeRows = (rows) => Array.isArray(rows) ? rows.map(normalizeRepairOrderRow).filter(repairOrderRowHasAnyData) : [];
      const payments = normalizeRepairOrderPayments(
        source.payments ?? source.payment_history ?? [],
        source.prepayment ?? source.advance_payment ?? source.advancePayment ?? '',
        String(source.opened_at ?? source.openedAt ?? source.date ?? '').trim()
      );
      const paymentMethod = repairOrderPaymentMethodFromPayments(
        payments,
        source.payment_method ?? source.paymentMethod ?? 'cash'
      );
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
        payment_method: paymentMethod,
        prepayment: repairOrderNumberToRaw(repairOrderPaymentsTotalValue(payments)),
        payments,
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
        normalized.prepayment ||
        normalized.payments.length ||
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
      const inlineMatch = normalized.match(/^(\\d{2})\\.(\\d{2})\\.(\\d{2}|\\d{4})(?:[,\\s]+(\\d{2}):(\\d{2})(?::\\d{2})?)?$/);
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
      return canonical.replace(/^(\\d{2}\\.\\d{2}\\.)\\d{2}(\\d{2}\\s+\\d{2}:\\d{2})$/, '$1$2');
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
      const canonical = repairOrderCanonicalDateValue(value);
      if (!canonical) return '';
      return canonical.split(' ')[0] || canonical;
    }

    function repairOrderStatusLabel(status) {
      return String(status || '').trim().toLowerCase() === 'closed' ? 'Закрыт' : 'Открыт';
    }

    function repairOrderCloseBlockedMessage() {
      return 'Для закрытия заказ-наряда необходимо выполнить оплату.';
    }

    function repairOrderIsFullyPaid(order) {
      const normalized = normalizeRepairOrder(order);
      const subtotal = repairOrderRoundMoney(
        repairOrderRowsTotalValue(normalized.works) + repairOrderRowsTotalValue(normalized.materials)
      );
      const taxes = repairOrderRoundMoney(
        repairOrderCashlessPaymentsValue(normalized.payments) * repairOrderTaxRate('cashless')
      );
      const grandTotal = repairOrderRoundMoney(subtotal + taxes);
      const paidTotal = repairOrderPaymentsTotalValue(normalized.payments);
      return paidTotal >= grandTotal;
    }

    function syncRepairOrderCloseButtonState(order = null) {
      const normalized = normalizeRepairOrder(order || readRepairOrderFromForm());
      const closeAvailable = normalized.status === 'closed' || repairOrderIsFullyPaid(normalized);
      els.repairOrderCloseButton.disabled = !closeAvailable;
      els.repairOrderCloseButton.dataset.closeAvailable = closeAvailable ? 'true' : 'false';
      els.repairOrderCloseButton.title = closeAvailable ? '' : repairOrderCloseBlockedMessage();
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
        mileage: currentCard.repair_order?.mileage || (profile.mileage ?? ''),
        payment_method: currentCard.repair_order?.payment_method || 'cash',
        prepayment: currentCard.repair_order?.prepayment || '',
        payments: currentCard.repair_order?.payments || [],
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
        payment_method: resolvedField('payment_method', ['paymentMethod']),
        prepayment: resolvedField('prepayment', ['advance_payment', 'advancePayment']),
        payments: hasField('payments', ['payment_history']) ? normalized.payments : defaults.payments,
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
      return normalizedNumber ? ('ЗАКАЗ-НАРЯД №' + normalizedNumber) : 'ЗАКАЗ-НАРЯД';
    }

    function repairOrderCardRequiredMessageLegacy() {
      return 'Сначала сохраните карточку, чтобы открыть заказ-наряд.';
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

    function repairOrderExecutorOptionsHtml(selectedId, selectedName = '') {
      const options = ['<option value="">—</option>'];
      const employees = Array.isArray(state.employees) ? state.employees.filter((item) => item && item.is_active) : [];
      const rendered = new Set();
      employees.forEach((employee) => {
        const employeeId = String(employee.id || '').trim();
        if (!employeeId || rendered.has(employeeId)) return;
        rendered.add(employeeId);
        options.push(
          '<option value="' + escapeHtml(employeeId) + '"' + (employeeId === selectedId ? ' selected' : '') + '>' + escapeHtml(employee.name || 'Сотрудник') + '</option>'
        );
      });
      if (selectedId && !rendered.has(selectedId)) {
        options.push('<option value="' + escapeHtml(selectedId) + '" selected>' + escapeHtml(selectedName || 'Сотрудник') + '</option>');
      }
      return options.join('');
    }

    function repairOrderRowHtml(section, row, index) {
      const normalized = normalizeRepairOrderRow(row);
      const totalValue = repairOrderResolvedRowTotalValue(normalized);
      const hasDisplayTotal = totalValue !== null || Boolean(normalized.total);
      const catalogCell = section === 'materials'
        ? ('<td>' + repairOrderRowInputHtml('catalog_number', normalized.catalog_number, 'Артикул / OEM') + '</td>')
        : '';
      const executorCell = section === 'works'
        ? '<td><select class="repair-order-table__select" data-repair-order-cell="executor_id">' + repairOrderExecutorOptionsHtml(normalized.executor_id, normalized.executor_name) + '</select></td>'
        : '';
      return '<tr data-repair-order-row="' + escapeHtml(section) + '" data-repair-order-total-raw="' + escapeHtml(normalized.total) + '" data-repair-order-salary-mode="' + escapeHtml(normalized.salary_mode_snapshot) + '" data-repair-order-base-salary="' + escapeHtml(normalized.base_salary_snapshot) + '" data-repair-order-work-percent="' + escapeHtml(normalized.work_percent_snapshot) + '" data-repair-order-salary-amount="' + escapeHtml(normalized.salary_amount) + '" data-repair-order-salary-accrued-at="' + escapeHtml(normalized.salary_accrued_at) + '">' +
        '<td>' + repairOrderRowInputHtml('name', normalized.name, 'Наименование') + '</td>' +
        catalogCell +
        executorCell +
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
      const executorSelect = row.querySelector('[data-repair-order-cell="executor_id"]');
      const selectedOption = executorSelect instanceof HTMLSelectElement ? executorSelect.options[executorSelect.selectedIndex] : null;
      return normalizeRepairOrderRow({
        name: row.querySelector('[data-repair-order-cell="name"]')?.value,
        catalog_number: row.querySelector('[data-repair-order-cell="catalog_number"]')?.value,
        quantity: row.querySelector('[data-repair-order-cell="quantity"]')?.value,
        price: row.querySelector('[data-repair-order-cell="price"]')?.value,
        total: row.dataset.repairOrderTotalRaw || '',
        executor_id: executorSelect instanceof HTMLSelectElement ? executorSelect.value : '',
        executor_name: selectedOption ? selectedOption.textContent : '',
        salary_mode_snapshot: row.dataset.repairOrderSalaryMode || '',
        base_salary_snapshot: row.dataset.repairOrderBaseSalary || '',
        work_percent_snapshot: row.dataset.repairOrderWorkPercent || '',
        salary_amount: row.dataset.repairOrderSalaryAmount || '',
        salary_accrued_at: row.dataset.repairOrderSalaryAccruedAt || '',
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
      const subtotal = repairOrderRoundMoney(worksTotal + materialsTotal);
      const cashlessInfo = repairOrderRoundMoney(subtotal * 1.15);
      const taxes = repairOrderRoundMoney(repairOrderCashlessPaymentsValue(state.repairOrderPayments) * repairOrderTaxRate('cashless'));
      const grandTotal = repairOrderRoundMoney(subtotal + taxes);
      const prepayment = repairOrderPaymentsTotalValue(state.repairOrderPayments);
      if (els.repairOrderPrepayment) {
        els.repairOrderPrepayment.value = repairOrderNumberToRaw(prepayment);
      }
      const dueTotal = repairOrderRoundMoney(grandTotal - prepayment);
      document.querySelectorAll('[data-repair-order-total="subtotal"]').forEach((node) => {
        node.textContent = repairOrderFormatMoney(subtotal);
      });
      document.querySelectorAll('[data-repair-order-total="cashless"]').forEach((node) => {
        node.textContent = repairOrderFormatMoney(cashlessInfo);
      });
      document.querySelectorAll('[data-repair-order-total="taxes"]').forEach((node) => {
        node.textContent = repairOrderFormatMoney(taxes);
      });
      document.querySelectorAll('[data-repair-order-total="prepayment"]').forEach((node) => {
        node.textContent = repairOrderFormatMoney(prepayment);
      });
      document.querySelectorAll('[data-repair-order-total="grand"]').forEach((node) => {
        node.textContent = repairOrderFormatMoney(grandTotal);
      });
      document.querySelectorAll('[data-repair-order-total="due"]').forEach((node) => {
        node.textContent = repairOrderFormatMoney(dueTotal);
      });
      document.querySelectorAll('[data-repair-order-total-block="taxes"]').forEach((node) => {
        node.classList.toggle('is-hidden', taxes === 0);
      });
      syncRepairOrderCloseButtonState();
    }

    function renderRepairOrderPayments() {
      const payments = Array.isArray(state.repairOrderPayments) ? state.repairOrderPayments : [];
      syncRepairOrderPaymentMethodFromPayments();
      const total = repairOrderPaymentsTotalValue(payments);
      const subtotal = repairOrderRoundMoney(syncRepairOrderSectionTotals('works') + syncRepairOrderSectionTotals('materials'));
      const taxes = repairOrderRoundMoney(repairOrderCashlessPaymentsValue(payments) * repairOrderTaxRate('cashless'));
      const due = repairOrderRoundMoney(subtotal + taxes - total);
      if (els.repairOrderPaymentsMeta) {
        const latestPayment = payments.length ? payments[payments.length - 1] : null;
        const latestText = latestPayment
          ? ('Последняя оплата: ' + (String(latestPayment?.paid_at || '').trim() || 'дата не указана') + ' | Кем: ' + (String(latestPayment?.actor_name || '').trim() || 'оператор не указан') + ' | Касса: ' + (String(latestPayment?.cashbox_name || '').trim() || 'касса не указана'))
          : 'Пока нет оплат. Добавьте первое поступление в выбранную кассу.';
        els.repairOrderPaymentsMeta.innerHTML =
          '<div class="repair-order-payments-stats">'
            + '<div class="repair-order-payments-stat"><span>Оплат</span><strong>' + escapeHtml(String(payments.length)) + '</strong></div>'
            + '<div class="repair-order-payments-stat"><span>Внесено</span><strong>' + escapeHtml(repairOrderFormatMoney(total)) + '</strong></div>'
            + '<div class="repair-order-payments-stat"><span>К доплате</span><strong>' + escapeHtml(repairOrderFormatMoney(due)) + '</strong></div>'
          + '</div>'
          + '<div class="repair-order-payments-subline">' + escapeHtml(latestText) + '</div>';
      }
      if (els.repairOrderPaymentsList) {
        els.repairOrderPaymentsList.innerHTML = payments.length ? payments.slice().reverse().map((item) => {
          const note = String(item?.note || '').trim() || 'Без комментария';
          const paidAt = String(item?.paid_at || '').trim() || 'Дата не указана';
          const method = repairOrderPaymentMethodLabel(item?.payment_method || 'cash');
          const actorName = String(item?.actor_name || '').trim() || 'Оператор не указан';
          const cashboxName = String(item?.cashbox_name || '').trim() || 'Касса не указана';
          return '<div class="repair-order-payment-row">'
            + '<div class="repair-order-payment-row__badge">' + escapeHtml(method) + '</div>'
            + '<div class="repair-order-payment-row__body">'
              + '<div class="repair-order-payment-row__line">' + escapeHtml(note) + '</div>'
              + '<div class="repair-order-payment-row__subline">' + escapeHtml('Когда: ' + paidAt + ' | Кем: ' + actorName + ' | Касса: ' + cashboxName) + '</div>'
            + '</div>'
            + '<div class="repair-order-payment-row__amount">' + escapeHtml(repairOrderFormatMoney(item?.amount || 0)) + '</div>'
            + '<button class="btn btn--ghost repair-order-payment-row__remove" type="button" data-remove-repair-order-payment="' + escapeHtml(item.id) + '">?</button>'
            + '</div>';
        }).join('') : '<div class="cashboxes-empty">Оплат пока нет.</div>';
      }
      syncRepairOrderTotals();
    }

    async function openRepairOrderPaymentsModal() {
      ensureRepairOrderPaymentsModalUi();
      bindRepairOrderPaymentsUiEvents();
      try {
        await ensureRepairOrderPaymentCashboxes();
      } catch (error) {
        setStatus(error.message, true);
      }
      renderRepairOrderPayments();
      els.repairOrderPaymentsModal.classList.add('is-open');
      window.setTimeout(() => els.repairOrderPaymentAmount?.focus(), 0);
    }

    function closeRepairOrderPaymentsModal() {
      els.repairOrderPaymentsModal?.classList.remove('is-open');
      if (els.repairOrderPaymentAmount) els.repairOrderPaymentAmount.value = '';
      if (els.repairOrderPaymentNote) els.repairOrderPaymentNote.value = '';
    }

    function deleteRepairOrderPayment(paymentId) {
      state.repairOrderPayments = (state.repairOrderPayments || []).filter((item) => item.id !== paymentId);
      renderRepairOrderPayments();
    }

    function addRepairOrderPayment() {
      const amount = String(els.repairOrderPaymentAmount?.value || '').trim();
      const parsedAmount = repairOrderParseNumber(amount);
      const cashboxId = String(els.repairOrderPaymentCashbox?.value || '').trim();
      if (parsedAmount === null || parsedAmount <= 0) {
        setStatus('Укажите сумму оплаты.', true);
        els.repairOrderPaymentAmount?.focus();
        return;
      }
      if (!cashboxId) {
        setStatus('Выберите кассу для оплаты.', true);
        els.repairOrderPaymentCashbox?.focus();
        return;
      }
      const selectedCashbox = selectedRepairOrderPaymentCashbox();
      const paymentMethod = repairOrderPaymentMethodFromCashboxName(selectedCashbox?.name || '', 'cash');
      const payment = normalizeRepairOrderPayment(
        {
          id: 'payment-' + Date.now(),
          amount: repairOrderNumberToRaw(parsedAmount),
          paid_at: currentRepairOrderDateTime(),
          note: String(els.repairOrderPaymentNote?.value || '').trim(),
          payment_method: paymentMethod,
          actor_name: state.actor || '',
          cashbox_id: cashboxId,
          cashbox_name: selectedCashbox?.name || '',
        },
        'payment-' + Date.now()
      );
      state.repairOrderPayments = (state.repairOrderPayments || []).concat([payment]);
      syncRepairOrderPaymentMethodFromPayments();
      if (els.repairOrderPaymentAmount) els.repairOrderPaymentAmount.value = '';
      if (els.repairOrderPaymentNote) els.repairOrderPaymentNote.value = '';
      renderRepairOrderPayments();
      els.repairOrderPaymentAmount?.focus();
    }

    function handleRepairOrderPaymentsListClick(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const removeButton = target.closest('[data-remove-repair-order-payment]');
      if (!removeButton) return;
      deleteRepairOrderPayment(removeButton.dataset.removeRepairOrderPayment);
    }

    function handleRepairOrderPaymentsFormChange(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      if (target === els.repairOrderPaymentCashbox) {
        renderRepairOrderPayments();
      }
    }

    function syncRepairOrderStatusUi(status) {
      const normalizedStatus = String(status || '').trim().toLowerCase() === 'closed' ? 'closed' : 'open';
      els.repairOrderStatus.textContent = repairOrderStatusLabel(normalizedStatus);
      els.repairOrderStatus.dataset.status = normalizedStatus;
      els.repairOrderCloseButton.textContent = normalizedStatus === 'closed' ? 'ОТКРЫТЬ ЗАКАЗ-НАРЯД' : 'ЗАКРЫТЬ ЗАКАЗ-НАРЯД';
      syncRepairOrderCloseButtonState({
        ...readRepairOrderFromForm(),
        status: normalizedStatus,
      });
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
      state.repairOrderPayments = normalizeRepairOrderPayments(normalized.payments, normalized.prepayment, normalized.opened_at || normalized.date);
      syncRepairOrderPaymentMethod(repairOrderPaymentMethodFromPayments(state.repairOrderPayments, normalized.payment_method));
      renderRepairOrderPaymentCashboxOptions('');
      if (els.repairOrderPrepayment) {
        els.repairOrderPrepayment.value = repairOrderNumberToRaw(repairOrderPaymentsTotalValue(state.repairOrderPayments));
      }
      els.repairOrderReason.value = normalized.reason;
      els.repairOrderComment.value = normalized.comment;
      els.repairOrderNote.value = normalized.note;
      state.repairOrderTags = normalizeRepairOrderTags(normalized.tags);
      state.repairOrderTagColor = state.repairOrderTags[0]?.color || 'green';
      renderRepairOrderTags();
      renderRepairOrderPayments();
      renderRepairOrderRows('works', normalized.works);
      renderRepairOrderRows('materials', normalized.materials);
      const heading = repairOrderHeading(normalized.number);
      els.repairOrderModalTitle.textContent = heading;
      els.repairOrderModalTitle.title = heading;
      syncRepairOrderStatusUi(normalized.status);
      syncRepairOrderTotals();
    }

    function readRepairOrderFromForm() {
      const paymentMethod = repairOrderPaymentMethodFromPayments(state.repairOrderPayments, 'cash');
      syncRepairOrderPaymentMethod(paymentMethod);
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
        payment_method: paymentMethod,
        prepayment: repairOrderNumberToRaw(repairOrderPaymentsTotalValue(state.repairOrderPayments)),
        payments: (state.repairOrderPayments || []).map((item, index) => normalizeRepairOrderPayment({
          ...item,
        }, item?.id || ('payment-' + (index + 1)))),
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

    async function openRepairOrderModal() {
      try {
        await loadEmployeesReference();
      } catch (error) {
        setStatus(error.message, true);
      }
      let order = repairOrderCardDraft(state.activeCard, state.activeCard?.repair_order || {});
      const cardId = String(state.activeCard?.id || state.editingId || '').trim();
      if (cardId) {
        try {
          const data = await api('/api/get_repair_order', {
            method: 'POST',
            body: {
              card_id: cardId,
              actor_name: state.actor,
              source: 'ui',
            },
          });
          const updatedCard = repairOrderResponseCard(data, order);
          order = applyRepairOrderCardUpdate(updatedCard, data?.repair_order || order);
        } catch (error) {
          setStatus(error.message, true);
          return;
        }
      }
      applyRepairOrderToForm(order);
      els.repairOrderModal.classList.add('is-open');
    }

    function closeRepairOrderModal() {
      els.repairOrderModal.classList.remove('is-open');
      closeRepairOrderPaymentsModal();
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
        const currentOrder = readRepairOrderFromForm();
        const currentStatus = currentOrder.status;
        const nextStatus = currentStatus === 'closed' ? 'open' : 'closed';
        if (nextStatus === 'closed' && !repairOrderIsFullyPaid(currentOrder)) {
          setStatus(repairOrderCloseBlockedMessage(), true);
          return;
        }
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
        syncRepairOrderCloseButtonState();
      }
    }

    function columnLabelById(columnId) {
      const found = (state.snapshot?.columns || []).find((column) => column.id === columnId);
      return found?.label || String(columnId || '—');
    }

    function renderCardCleanupIndicator() {
      if (!(els.cardAgentButton instanceof HTMLElement)) return;
      const currentState = String(state.cardCleanupState || 'idle').trim().toLowerCase();
      const uiState = currentState === 'running'
        ? 'busy'
        : (currentState === 'error' ? 'error' : (state.activeCard?.id ? 'online' : 'idle'));
      let title = 'Прибраться в карточке';
      if (currentState === 'running') {
        title = 'Прибраться в карточке · выполняется';
      } else if (currentState === 'error') {
        title = state.cardCleanupError
          ? 'Прибраться в карточке · ошибка: ' + String(state.cardCleanupError || '').trim()
          : 'Прибраться в карточке · ошибка';
      } else if (state.activeCard?.id) {
        title = 'Прибраться в карточке · готово';
      }
      els.cardAgentButton.dataset.state = uiState;
      els.cardAgentButton.title = title;
      els.cardAgentButton.setAttribute('aria-label', title);
      els.cardAgentButton.disabled = currentState === 'running' || !state.activeCard?.id;
    }

    async function runCardCleanup() {
      if (!requireOperatorSession()) return;
      const cardId = String(state.activeCard?.id || '').trim();
      if (!cardId) {
        return setStatus('ОТКРОЙ КАРТОЧКУ ДЛЯ УБОРКИ.', true);
      }
      state.cardCleanupState = 'running';
      state.cardCleanupError = '';
      renderCardCleanupIndicator();
      try {
        const data = await api('/api/cleanup_card_content', {
          method: 'POST',
          body: { card_id: cardId },
        });
        if (data?.card) {
          state.activeCard = data.card;
          if (els.cardModal?.classList.contains('is-open')) {
            applyCardModalState(data.card);
          }
        }
        state.cardCleanupState = 'idle';
        renderCardCleanupIndicator();
        const changed = Boolean(data?.meta?.changed);
        setStatus(changed ? 'Карточка приведена в порядок.' : 'Явных изменений для карточки не найдено.', false);
        await refreshSnapshot(false);
      } catch (error) {
        state.cardCleanupState = 'error';
        state.cardCleanupError = String(error?.message || 'Не удалось прибраться в карточке.');
        renderCardCleanupIndicator();
        setStatus(state.cardCleanupError, true);
      }
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
      const parts = secondsToParts(currentCard?.remaining_seconds || 86400);
      els.signalDays.value = parts.days;
      els.signalHours.value = parts.hours;
      renderSignalPreview();
      els.cardMetaLine.textContent = currentCard?.id ? ('создано ' + formatDate(currentCard.created_at) + ' · изменено ' + formatDate(currentCard.updated_at)) : 'новая запись';
      els.archiveAction.classList.toggle('hidden', !currentCard?.id || currentCard.archived);
      els.restoreAction.classList.toggle('hidden', !currentCard?.id || !currentCard.archived);
      state.vehicleProfileBaseline = cloneVehicleProfile(currentCard?.vehicle_profile || {});
      applyVehicleProfileToForm(currentCard?.vehicle_profile || emptyVehicleProfile());
      refreshRepairOrderEntry(currentCard);
      renderColorTags();
      renderFiles(currentCard);
      renderLogs([]);
      if (state.cardCleanupState !== 'error') {
        state.cardCleanupState = currentCard?.id ? 'idle' : 'idle';
        state.cardCleanupError = '';
      }
      renderCardCleanupIndicator();
    }

    function resetCardModalState() {
      state.activeCard = null;
      state.editingId = null;
      state.vehicleProfileDraft = null;
      state.vehicleProfileBaseline = null;
      state.vehicleAutofillResult = null;
      state.draftTags = [];
      state.draftTagColor = 'green';
      state.cardCleanupState = 'idle';
      state.cardCleanupError = '';
      refreshRepairOrderEntry(null);
      els.fileInput.value = '';
      syncFileDropzone(null);
      renderCardCleanupIndicator();
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
        ? state.draftTags.map((tag) => '<button class="tag" data-remove-tag="' + escapeHtml(tag) + '">' + escapeHtml(tag) + ' ?</button>').join('')
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
        ? state.draftTags.map((tag) => '<button class="tag" data-tag-color="' + escapeHtml(tag.color) + '" data-remove-tag="' + escapeHtml(tag.label) + '"><span class="tag__dot"></span>' + escapeHtml(tag.label) + ' ?</button>').join('')
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
        ? 'Ctrl+V, правый клик -> Вставить, drag-and-drop или клик для выбора. PNG, JPG, JPEG, WEBP, GIF, TXT, PDF, Word, Excel.'
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
      const cards = Array.isArray(state.archiveCards) ? state.archiveCards : [];
      els.archiveList.innerHTML = cards.length
        ? cards.map((card) => '<div class="archive-row"><div><strong>' + escapeHtml(cardHeading(card)) + '</strong></div><div>' + escapeHtml(card.description || 'Описание не указано') + '</div><div class="archive-row__meta">АРХИВ: ' + escapeHtml(formatDate(card.updated_at)) + '</div><div style="display:flex; gap:8px;"><button class="btn" data-restore-card="' + escapeHtml(card.id) + '">ВЕРНУТЬ</button></div></div>').join('')
        : '<div class="log-row__meta">АРХИВ ПУСТ.</div>';
    }

    function legacyRenderRepairOrderRowsExpandedShadow(items) {
      return items.map((item) => '<div class="archive-row repair-orders-row" role="button" tabindex="0" data-open-repair-order-card="' + escapeHtml(item.card_id) + '" title="Открыть заказ-наряд"><div class="repair-orders-row__number">№ ' + escapeHtml(item.number || '-') + '</div><div class="repair-orders-row__vehicle" title="' + escapeHtml(item.vehicle || '-') + '">' + escapeHtml(item.vehicle || 'Авто не указано') + '</div><div class="repair-orders-row__title" title="' + escapeHtml(item.heading || 'Заказ-наряд') + '">' + escapeHtml(item.heading || 'Заказ-наряд') + '</div></div>').join('');
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
      const cards = Array.isArray(state.archiveCards) ? state.archiveCards : [];
      els.archiveList.innerHTML = cards.length
        ? cards.map((card) => {
            const heading = cardHeading(card);
            const compactDescription = String(card.description || 'Описание не указано').replace(/\\s+/g, ' ').trim();
            const summary = compactDescription.length > 180 ? compactDescription.slice(0, 177) + '...' : compactDescription;
            return '<div class="archive-row archive-row--compact"><div class="archive-row__main"><div class="archive-row__title" title="' + escapeHtml(heading) + '">' + escapeHtml(heading) + '</div><div class="archive-row__summary" title="' + escapeHtml(compactDescription || 'Описание не указано') + '">' + escapeHtml(summary || 'Описание не указано') + '</div></div><div class="archive-row__side"><div class="archive-row__meta">АРХИВ: ' + escapeHtml(formatDate(card.updated_at)) + '</div><button class="btn" data-restore-card="' + escapeHtml(card.id) + '">ВЕРНУТЬ</button></div></div>';
          }).join('')
        : '<div class="log-row__meta">АРХИВ ПУСТ.</div>';
    };

function renderCompactArchiveRows(cards) {
      return cards.map((card) => {
        const heading = cardHeading(card);
        const compactDescription = String(card.description || 'Описание не указано').replace(/\\s+/g, ' ').trim();
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
      if (!events.length) return 'СОБЫТИЙ НЕТ.';
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
        if (event?.details_text) lines.push('details: ' + String(event.details_text).replace(/\\r?\\n/g, ' / '));
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
      const cards = Array.isArray(state.archiveCards) ? state.archiveCards : [];
      els.archiveList.innerHTML = cards.length
        ? renderCompactArchiveRows(cards)
        : state.archiveLoading
          ? '<div class="log-row__meta">ЗАГРУЗКА АРХИВА...</div>'
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
      if (openRepairOrder) await openRepairOrderModal();
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
      syncRepairOrdersLayout(isClosed ? 'closed' : 'open');
    }

    function repairOrdersIsClosedView(status = state.repairOrdersFilter) {
      return String(status || '').trim().toLowerCase() === 'closed';
    }

    function repairOrdersColumnsValue(status = state.repairOrdersFilter) {
      repairOrdersIsClosedView(status);
      return 'minmax(56px, 72px) minmax(132px, 160px) minmax(92px, 108px) minmax(108px, 124px) minmax(140px, 176px) minmax(124px, 146px) minmax(150px, 188px) minmax(320px, 2.8fr) minmax(88px, 104px) minmax(88px, 104px)';
    }

    function repairOrdersTableHeadHtml(status = state.repairOrdersFilter) {
      return '<div>Номер</div>'
        + '<div>Даты</div>'
        + '<div>Статус</div>'
        + '<div>Оплата</div>'
        + '<div>Клиент</div>'
        + '<div>Телефон</div>'
        + '<div>Автомобиль</div>'
        + '<div>Смысл карточки</div>'
        + '<div class="repair-orders-table-head__sum">Внесено</div>'
        + '<div class="repair-orders-table-head__sum">Сумма</div>';
    }
    function syncRepairOrdersLayout(status = state.repairOrdersFilter) {
      if (els.repairOrdersModal) {
        els.repairOrdersModal.style.setProperty('--repair-orders-columns', repairOrdersColumnsValue(status));
      }
      if (els.repairOrdersTableHead) {
        els.repairOrdersTableHead.innerHTML = repairOrdersTableHeadHtml(status);
      }
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

    // СПИСОК: ДАТА / АВТО / СУТЬ / СУММА
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
      parts.push('СОРТ: ' + sortLabel + ' ' + (sortDir === 'asc' ? '^' : 'v'));
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
        const clientText = client || '-';
        const phoneText = phone || '-';
        const heading = item.summary || item.reason || item.heading || '-';
        const total = repairOrderListTotalText(item.grand_total, item.works_total);
        const paidTotal = repairOrderListTotalText(item.paid_total_display, item.paid_total);
        const paymentStatus = String(item.payment_status || '').trim().toLowerCase() === 'paid' ? 'paid' : 'unpaid';
        const paymentStatusLabel = String(item.payment_status_label || '').trim() || (paymentStatus === 'paid' ? 'Оплачен' : 'Не оплачен');
        const status = item.status_label || repairOrderStatusLabel(item.status);
        const rawStatus = String(item.status || 'open').trim().toLowerCase() === 'closed' ? 'closed' : 'open';
        const closedMeta = rawStatus === 'closed'
          ? ('Закрыта: ' + (closedAt || '-'))
          : 'Закрыта: -';
        const allTags = normalizeRepairOrderTags(item.tags || []);
        const previewTags = allTags.slice(0, 3);
        const extraTags = allTags.length - previewTags.length;
        const tagsHtml = previewTags.length
          ? '<div class="repair-orders-row__tags">' + previewTags.map((tag) => '<span class="tag" data-tag-color="' + escapeHtml(tag.color) + '"><span class="tag__dot"></span>' + escapeHtml(tag.label) + '</span>').join('') + (extraTags > 0 ? '<span class="tag">+' + extraTags + '</span>' : '') + '</div>'
          : '';
        return '<div class="archive-row repair-orders-row" role="button" tabindex="0" data-open-repair-order-card="' + escapeHtml(item.card_id) + '" title="Open repair order">'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__number">№ ' + escapeHtml(number) + '</div></div>'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__dates"><div class="repair-orders-row__opened">' + escapeHtml(openedAt || '-') + '</div><div class="repair-orders-row__date-meta">' + escapeHtml(closedMeta) + '</div></div></div>'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__status" data-status="' + escapeHtml(rawStatus) + '">' + escapeHtml(status) + '</div></div>'
          + '<div class="repair-orders-row__cell repair-orders-row__payment-cell"><div class="repair-orders-row__payment-status" data-payment-status="' + escapeHtml(paymentStatus) + '">' + escapeHtml(paymentStatusLabel) + '</div></div>'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__client" title="' + escapeHtml(clientText) + '">' + escapeHtml(clientText) + '</div></div>'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__phone" title="' + escapeHtml(phoneText) + '">' + escapeHtml(phoneText) + '</div></div>'
          + '<div class="repair-orders-row__cell"><div class="repair-orders-row__vehicle" title="' + escapeHtml(vehicle) + '">' + escapeHtml(vehicle) + '</div></div>'
          + '<div class="repair-orders-row__cell repair-orders-row__title-cell"><div class="repair-orders-row__title" title="' + escapeHtml(heading) + '">' + escapeHtml(heading) + '</div>' + tagsHtml + '</div>'
          + '<div class="repair-orders-row__cell repair-orders-row__paid-cell"><div class="repair-orders-row__paid" data-empty="' + String(paidTotal === '0') + '">' + escapeHtml(paidTotal) + '</div></div>'
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
      return '<article class="card" draggable="true" data-card-id="' + escapeHtml(card.id) + '" data-indicator="' + escapeHtml(card.indicator) + '" data-status="' + escapeHtml(card.status) + '" data-blink="' + (card.is_blinking ? 'true' : 'false') + '">' + headingHtml + '<div class="card__desc">' + escapeHtml(card.description || 'Описание не указано') + '</div><div class="card__signal"><span class="card__signal-label"><span class="lamp" data-indicator="' + escapeHtml(card.indicator) + '"></span><span>СИГН</span></span><span class="card__signal-value">' + durationToMarkup(card.remaining_seconds, false) + '</span></div><div class="card__tags">' + tagsHtml + '</div></article>';
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
      return '<article class="card" style="' + heatStyle + '" draggable="true" data-card-id="' + escapeHtml(card.id) + '" data-indicator="' + escapeHtml(card.indicator) + '" data-status="' + escapeHtml(card.status) + '" data-blink="' + (card.is_blinking ? "true" : "false") + '" data-deadline-bucket="' + escapeHtml(card.deadline_progress_bucket ?? 0) + '" data-deadline-step="' + escapeHtml(card.deadline_progress_step_percent ?? 0) + '">' + headingHtml + '<div class="card__desc">' + escapeHtml(card.description || 'Описание не указано') + '</div><div class="card__signal"><span class="card__signal-label"><span class="lamp" data-indicator="' + escapeHtml(card.indicator) + '"></span><span>СИГН</span></span><span class="card__signal-value">' + durationToMarkup(card.remaining_seconds, false) + '</span></div><div class="card__tags">' + tagsHtml + '</div></article>';
    }

    function renderBoardCardHtml(card) {
      const normalizedTags = normalizeDraftTags(card.tag_items || card.tags || []);
      const previewTags = normalizedTags.slice(0, CARD_TAG_LIMIT);
      const extraTags = normalizedTags.length - previewTags.length;
      const tagsHtml = previewTags.length
        ? previewTags.map((tag) => '<span class="tag" data-tag-color="' + escapeHtml(tag.color) + '"><span class="tag__dot"></span>' + escapeHtml(tag.label) + '</span>').join('') + (extraTags > 0 ? '<span class="tag">+' + extraTags + '</span>' : '')
        : '<span class="tag tag--muted">БЕЗ МЕТОК</span>';
      const headingHtml = buildCardHeadingHtml(card);
      const unreadBadgeHtml = cardUnreadBadgeHtml(card);
      const heatStyle = '--deadline-heat-border:' + escapeHtml(card.deadline_heat_border_color || 'rgba(83, 191, 122, 0.34)') + ';--deadline-heat-ring:' + escapeHtml(card.deadline_heat_ring_color || 'rgba(83, 191, 122, 0.08)') + ';--deadline-heat-glow:' + escapeHtml(card.deadline_heat_glow_color || 'rgba(83, 191, 122, 0.04)') + ';';
      return '<article class="card" style="' + heatStyle + '" draggable="true" data-card-id="' + escapeHtml(card.id) + '" data-indicator="' + escapeHtml(card.indicator) + '" data-status="' + escapeHtml(card.status) + '" data-blink="' + (card.is_blinking ? "true" : "false") + '" data-unread="' + (card.is_unread ? 'true' : 'false') + '" data-deadline-bucket="' + escapeHtml(card.deadline_progress_bucket ?? 0) + '" data-deadline-step="' + escapeHtml(card.deadline_progress_step_percent ?? 0) + '">' + unreadBadgeHtml + headingHtml + '<div class="card__desc">' + escapeHtml(card.description || 'Описание не указано') + '</div><div class="card__signal"><span class="card__signal-label"><span class="lamp" data-indicator="' + escapeHtml(card.indicator) + '"></span><span>СИГН</span></span><span class="card__signal-value">' + durationToMarkup(card.remaining_seconds, false) + '</span></div><div class="card__tags">' + tagsHtml + '</div></article>';
    }

    function sortBoardCards(cards) {
      return (Array.isArray(cards) ? cards : []).slice().sort((left, right) =>
        ((left.position ?? 0) - (right.position ?? 0))
        || String(left.created_at || '').localeCompare(String(right.created_at || ''))
        || String(left.id || '').localeCompare(String(right.id || ''))
      );
    }

    function sortBoardColumns(columns) {
      return (Array.isArray(columns) ? columns : []).slice().sort((left, right) =>
        ((left.position ?? 0) - (right.position ?? 0))
        || String(left.label || '').localeCompare(String(right.label || ''), 'ru')
        || String(left.id || '').localeCompare(String(right.id || ''))
      );
    }

    function buildBoardCardsByColumn(snapshot) {
      const grouped = new Map();
      (snapshot?.cards || []).forEach((card) => {
        const columnId = String(card?.column || '').trim();
        if (!columnId) return;
        const bucket = grouped.get(columnId);
        if (bucket) bucket.push(card);
        else grouped.set(columnId, [card]);
      });
      grouped.forEach((bucket, columnId) => grouped.set(columnId, sortBoardCards(bucket)));
      return grouped;
    }

    function sortedCardsForBoardColumn(snapshot, columnId, cardsByColumn = null) {
      if (cardsByColumn instanceof Map) return cardsByColumn.get(columnId) || [];
      const cards = (snapshot?.cards || []).filter((card) => card.column === columnId);
      return sortBoardCards(cards);
    }

    function renderBoardColumnHtml(column, index, snapshot, cardsByColumn = null) {
      const cards = sortedCardsForBoardColumn(snapshot, column.id, cardsByColumn);
      const tone = COLUMN_TONES[index % COLUMN_TONES.length];
      const toneStyle = '--column-tint:' + tone.tint + ';--column-head:' + tone.head + ';--column-edge:' + tone.edge + ';--column-empty:' + tone.empty + ';';
      const isDeleteBlocked = cards.length > 0 || snapshot.columns.length <= 1;
      const deleteTitle = cards.length > 0
        ? 'Сначала убери карточки из этого столбца'
        : (snapshot.columns.length <= 1 ? 'Последний столбец нельзя удалить' : 'Удалить пустой столбец');
      const renameTitle = 'Переименовать столбец';
      const deleteAttrs = isDeleteBlocked ? ' disabled' : '';
      return '<section class="column" style="' + toneStyle + '" data-column-id="' + escapeHtml(column.id) + '" draggable="true"><div class="column__head" data-drag-column-handle="1"><div class="column__title">' + escapeHtml(column.label) + '</div><div class="column__head-actions"><button class="btn btn--ghost column__rename" type="button" data-rename-column="' + escapeHtml(column.id) + '" data-column-label="' + escapeHtml(column.label) + '" title="' + escapeHtml(renameTitle) + '" aria-label="' + escapeHtml(renameTitle) + '">&#9998;</button><button class="btn btn--ghost column__delete" type="button" data-delete-column="' + escapeHtml(column.id) + '" data-column-label="' + escapeHtml(column.label) + '" data-card-count="' + cards.length + '" title="' + escapeHtml(deleteTitle) + '" aria-label="' + escapeHtml(deleteTitle) + '"' + deleteAttrs + '>?</button><div class="column__count">' + cards.length + '</div></div></div><div class="column__cards">' + (cards.length ? cards.map(renderBoardCardHtml).join('') : '<div class="empty">ЗДЕСЬ ПОКА ПУСТО.</div>') + '</div><button class="btn" data-create-in="' + escapeHtml(column.id) + '">+ КАРТОЧКА</button></section>';
    }

    function renderBoardColumnById(columnId, cardsByColumn = null) {
      const snapshot = state.snapshot;
      if (!snapshot || !columnId) return false;
      const columnIndex = snapshot.columns.findIndex((column) => column.id === columnId);
      if (columnIndex < 0) return false;
      const currentSection = els.board.querySelector('[data-column-id="' + columnId + '"]');
      if (!currentSection) return false;
      const template = document.createElement('template');
      template.innerHTML = renderBoardColumnHtml(snapshot.columns[columnIndex], columnIndex, snapshot, cardsByColumn);
      const nextSection = template.content.firstElementChild;
      if (!nextSection) return false;
      currentSection.replaceWith(nextSection);
      return true;
    }

    function renderBoard() {
      const snapshot = state.snapshot;
      if (!snapshot) return;
      const cardsByColumn = buildBoardCardsByColumn(snapshot);
      els.board.innerHTML = snapshot.columns.map((column, index) => renderBoardColumnHtml(column, index, snapshot, cardsByColumn)).join('') + '<div class="sticky-layer" id="stickyLayer"></div>';
      els.stickyLayer = document.getElementById('stickyLayer');
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
    window.__closeCardModal = closeCardModal;

    function bindDirectCardModalCloseButtons() {
      [els.cardModalCloseButtonTop, els.cardModalCloseButtonBottom].forEach((button) => {
        if (!(button instanceof HTMLElement)) return;
        button.addEventListener('click', (event) => {
          event.preventDefault();
          event.stopPropagation();
          closeCardModal();
        });
      });
    }

    async function refreshSnapshot(showSuccess = false) {
      if (state.refreshInFlight) {
        const pending = state.refreshInFlight;
        await pending;
        if (!showSuccess) return;
      }

      state.refreshInFlight = (async () => {
        try {
          const nextSnapshot = await api('/api/get_board_snapshot?compact=1&include_archive=0');
          const previousRevision = String(state.lastSnapshotRevision || '');
          const nextRevision = String(nextSnapshot?.meta?.revision || '');
          const boardChanged = !previousRevision || !nextRevision || previousRevision !== nextRevision;
          state.snapshot = nextSnapshot;
          applyBoardScalePreference({ fallbackValue: state.snapshot?.settings?.board_scale ?? 1, syncInput: true, persistFallback: true });
          if (boardChanged) {
            renderBoard();
            primeBoardViewport();
          }
          state.lastSnapshotRevision = nextRevision;
          if (els.archiveModal.classList.contains('is-open')) {
            await loadArchive(false, { force: true });
          }
          if (els.gptWallModal.classList.contains('is-open')) await loadGptWall(false);
          const data = state.snapshot;
        setStatus(showSuccess ? ('ДОСКА ОБНОВЛЕНА · ' + new Date().toLocaleTimeString('ru-RU')) : ('СЕРВЕР АКТИВЕН · КАРТОЧЕК: ' + data.cards.length + ' · АРХИВ: ' + archivedCardsTotal()));
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
      const archive = state.archiveCards || [];
      return cards.find((card) => card.id === cardId) || archive.find((card) => card.id === cardId) || null;
    }

    function boardCardElementById(cardId) {
      if (!cardId) return null;
      return els.board?.querySelector('.card[data-card-id="' + cardId + '"]') || null;
    }

    function replaceBoardCardElement(nextCard) {
      const currentCard = boardCardElementById(nextCard?.id);
      if (!currentCard) return false;
      const template = document.createElement('template');
      template.innerHTML = renderBoardCardHtml(nextCard);
      const nextCardElement = template.content.firstElementChild;
      if (!nextCardElement) return false;
      currentCard.replaceWith(nextCardElement);
      return true;
    }

    function updateSnapshotStatusLine({ showSuccess = false } = {}) {
      const data = state.snapshot;
      if (!data) return;
      setStatus(
        showSuccess
          ? ('ДОСКА ОБНОВЛЕНА · ' + new Date().toLocaleTimeString('ru-RU'))
          : ('СЕРВЕР АКТИВЕН · КАРТОЧЕК: ' + data.cards.length + ' · АРХИВ: ' + archivedCardsTotal()),
        false,
      );
    }

    function applyBoardColumnCardsPatch(nextCards, affectedColumnIds) {
      if (!Array.isArray(state.snapshot?.cards) || !Array.isArray(nextCards) || !Array.isArray(affectedColumnIds)) return false;
      const normalizedColumnIds = affectedColumnIds
        .map((value) => String(value || '').trim())
        .filter(Boolean);
      if (!normalizedColumnIds.length) return false;
      const targetColumns = new Set(normalizedColumnIds);
      const nextCardMap = new Map(nextCards.filter((card) => card?.id).map((card) => [card.id, card]));
      state.snapshot.cards = state.snapshot.cards
        .filter((card) => !targetColumns.has(String(card.column || '').trim()))
        .concat(nextCards);
      if (state.activeCard?.id) {
        const nextActiveCard = nextCardMap.get(state.activeCard.id);
        if (nextActiveCard) state.activeCard = nextActiveCard;
      }
      const cardsByColumn = buildBoardCardsByColumn(state.snapshot);
      let renderedAny = false;
      for (const columnId of normalizedColumnIds) {
        renderedAny = renderBoardColumnById(columnId, cardsByColumn) || renderedAny;
      }
      return renderedAny;
    }

    function applyBoardColumnsPatch(nextColumns) {
      if (!Array.isArray(state.snapshot?.columns) || !Array.isArray(nextColumns)) return false;
      state.snapshot.columns = sortBoardColumns(nextColumns);
      renderBoard();
      updateSnapshotStatusLine({ showSuccess: true });
      return true;
    }

    function applyArchivedCardPatch(nextCard) {
      if (!nextCard?.id || !Array.isArray(state.snapshot?.cards)) return false;
      const previousCard = snapshotCardById(nextCard.id);
      state.snapshot.cards = state.snapshot.cards.filter((card) => card.id !== nextCard.id);
      if (Array.isArray(state.archiveCards)) {
        state.archiveCards = state.archiveCards.filter((card) => card.id !== nextCard.id);
      }
      if (nextCard.archived) {
        if (state.archiveLoaded) {
          state.archiveCards = [nextCard].concat(state.archiveCards).slice(0, ARCHIVE_PREVIEW_LIMIT);
        }
      } else {
        state.snapshot.cards = state.snapshot.cards.concat(nextCard);
      }
      if (state.activeCard?.id === nextCard.id) state.activeCard = nextCard.archived ? null : nextCard;
      const affectedColumnId = String((nextCard.column || previousCard?.column || '')).trim();
      if (affectedColumnId) {
        const cardsByColumn = buildBoardCardsByColumn(state.snapshot);
        if (!renderBoardColumnById(affectedColumnId, cardsByColumn)) renderBoard();
      } else {
        renderBoard();
      }
      if (els.archiveModal.classList.contains('is-open')) renderArchive();
      updateSnapshotStatusLine();
      return true;
    }

    function applyStickySnapshot(stickies) {
      if (!Array.isArray(state.snapshot?.stickies) || !Array.isArray(stickies)) return false;
      state.snapshot.stickies = stickies;
      renderStickies();
      return true;
    }

    function replaceSnapshotCard(nextCard) {
      if (!nextCard?.id) return;
      const previousCard = snapshotCardById(nextCard.id);
      if (Array.isArray(state.snapshot?.cards)) {
        state.snapshot.cards = state.snapshot.cards.map((card) => card.id === nextCard.id ? nextCard : card);
      }
      if (Array.isArray(state.archiveCards)) {
        state.archiveCards = state.archiveCards.map((card) => card.id === nextCard.id ? nextCard : card);
      }
      if (state.activeCard?.id === nextCard.id) state.activeCard = nextCard;
      const archiveOpen = els.archiveModal.classList.contains('is-open');
      const touchesArchive = previousCard?.archived || nextCard.archived;
      if (archiveOpen && touchesArchive) renderArchive();
      if (!previousCard || previousCard.archived || nextCard.archived) {
        renderBoard();
        if (archiveOpen && !touchesArchive) renderArchive();
        return;
      }
      const previousColumnId = String(previousCard.column || '').trim();
      const nextColumnId = String(nextCard.column || '').trim();
      if (previousColumnId && previousColumnId === nextColumnId) {
        const previousPosition = Number(previousCard.position ?? NaN);
        const nextPosition = Number(nextCard.position ?? NaN);
        const samePosition = previousPosition === nextPosition || (Number.isNaN(previousPosition) && Number.isNaN(nextPosition));
        if (samePosition && replaceBoardCardElement(nextCard)) return;
        const cardsByColumn = buildBoardCardsByColumn(state.snapshot);
        if (!renderBoardColumnById(previousColumnId, cardsByColumn)) renderBoard();
        return;
      }
      const cardsByColumn = buildBoardCardsByColumn(state.snapshot);
      const renderedPrevious = previousColumnId ? renderBoardColumnById(previousColumnId, cardsByColumn) : false;
      const renderedNext = nextColumnId ? renderBoardColumnById(nextColumnId, cardsByColumn) : false;
      if (!renderedPrevious || !renderedNext) renderBoard();
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
        clearTimeout(state.pollHandle);
        state.pollHandle = null;
      }
    }

    function hasOpenWorkspaceModal() {
      return [
        els.cardModal,
        els.archiveModal,
        els.repairOrdersModal,
        els.cashboxesModal,
        els.employeesModal,
        els.agentModal,
        els.agentTasksModal,
        els.gptWallModal,
        els.boardSettingsModal,
        els.stickyModal,
        els.repairOrderModal,
        els.repairOrderPaymentsModal,
        els.operatorProfileModal,
        els.operatorAdminModal,
      ].some((modal) => modal?.classList.contains('is-open'));
    }

    function snapshotPollIntervalMs() {
      if (document.hidden) return SNAPSHOT_POLL_HIDDEN_INTERVAL_MS;
      if (hasOpenWorkspaceModal()) return SNAPSHOT_POLL_MODAL_INTERVAL_MS;
      return SNAPSHOT_POLL_INTERVAL_MS;
    }

    function scheduleNextSnapshotPoll() {
      stopSnapshotPolling();
      state.pollHandle = window.setTimeout(async () => {
        state.pollHandle = null;
        await refreshSnapshot(false);
        scheduleNextSnapshotPoll();
      }, snapshotPollIntervalMs());
    }

    function startSnapshotPolling() {
      scheduleNextSnapshotPoll();
    }

    function handleSnapshotVisibilityChange() {
      startSnapshotPolling();
      if (!document.hidden) refreshSnapshot(false);
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

    function clearColumnDropState() {
      state.boardDropBeforeColumnId = '';
      document.querySelectorAll('.column.is-column-drop-target').forEach((column) => column.classList.remove('is-column-drop-target'));
    }

    function finishColumnDrag() {
      clearColumnDropState();
      if (state.boardDragColumnId) {
        const dragged = document.querySelector('.column[data-column-id="' + state.boardDragColumnId + '"]');
        if (dragged) dragged.classList.remove('is-column-dragging');
      }
      state.boardDragColumnId = '';
    }

    function finishBoardDrag() {
      finishCardDrag();
      finishColumnDrag();
    }

    function updateBoardDragAutoScroll(clientX, clientY) {
      if ((!state.boardDragCardId && !state.boardDragColumnId) || !els.boardScroll) return;
      const rect = els.boardScroll.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const edgeThresholdX = Math.max(48, Math.min(96, Math.round(rect.width * 0.12)));
      const edgeThresholdY = Math.max(48, Math.min(96, Math.round(rect.height * 0.12)));
      const maxStepX = Math.max(18, Math.round(edgeThresholdX * 0.34));
      const maxStepY = Math.max(18, Math.round(edgeThresholdY * 0.34));
      let deltaX = 0;
      let deltaY = 0;
      if (clientX < rect.left + edgeThresholdX) {
        deltaX = -Math.round(((rect.left + edgeThresholdX) - clientX) / edgeThresholdX * maxStepX);
      } else if (clientX > rect.right - edgeThresholdX) {
        deltaX = Math.round((clientX - (rect.right - edgeThresholdX)) / edgeThresholdX * maxStepX);
      }
      if (clientY < rect.top + edgeThresholdY) {
        deltaY = -Math.round(((rect.top + edgeThresholdY) - clientY) / edgeThresholdY * maxStepY);
      } else if (clientY > rect.bottom - edgeThresholdY) {
        deltaY = Math.round((clientY - (rect.bottom - edgeThresholdY)) / edgeThresholdY * maxStepY);
      }
      if (!deltaX && !deltaY) return;
      clampBoardScroll(els.boardScroll.scrollLeft + deltaX, els.boardScroll.scrollTop + deltaY);
    }

    function resolveDropBeforeCardId(column, clientY, draggedCardId) {
      const cards = Array.from(column.querySelectorAll('.card')).filter((card) => card.dataset.cardId !== draggedCardId);
      for (const card of cards) {
        const rect = card.getBoundingClientRect();
        if (clientY < rect.top + (rect.height / 2)) return card.dataset.cardId || '';
      }
      return '';
    }

    function resolveDropBeforeColumnId(column, clientX, draggedColumnId) {
      const dragged = String(draggedColumnId || '').trim();
      const columns = Array.from(els.board?.querySelectorAll('.column[data-column-id]') || []);
      const hoveredId = String(column?.dataset?.columnId || '').trim();
      if (!hoveredId || hoveredId === dragged) return '';
      const rect = column.getBoundingClientRect();
      const insertBeforeHovered = clientX < rect.left + (rect.width / 2);
      if (insertBeforeHovered) return hoveredId;
      const orderedColumns = columns.filter((item) => String(item.dataset.columnId || '').trim() !== dragged);
      const hoveredIndex = orderedColumns.findIndex((item) => String(item.dataset.columnId || '').trim() === hoveredId);
      if (hoveredIndex < 0 || hoveredIndex === orderedColumns.length - 1) return '';
      return String(orderedColumns[hoveredIndex + 1].dataset.columnId || '').trim();
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
        const data = await api('/api/move_card', {
          method: 'POST',
          body: {
            card_id: cardId,
            column: columnId,
            before_card_id: beforeCardId || undefined,
            actor_name: state.actor,
            source: 'ui',
          },
        });
        const patched = applyBoardColumnCardsPatch(data?.affected_cards || [], data?.affected_column_ids || []);
        if (!patched && data?.card) {
          replaceSnapshotCard(data.card);
        }
        if (!patched && !data?.card) {
          await refreshSnapshot(true);
        } else {
          setStatus('ДОСКА ОБНОВЛЕНА · ' + new Date().toLocaleTimeString('ru-RU'), false);
        }
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        finishCardDrag();
      }
    }

    async function moveColumn(columnId, beforeColumnId = '') {
      try {
        const data = await api('/api/move_column', {
          method: 'POST',
          body: {
            column_id: columnId,
            before_column_id: beforeColumnId || undefined,
            actor_name: state.actor,
            source: 'ui',
          },
        });
        if (!applyBoardColumnsPatch(data?.columns || [])) {
          await refreshSnapshot(true);
        }
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        finishColumnDrag();
      }
    }

    async function restoreCard(cardId) {
      try {
        const data = await api('/api/restore_card', { method: 'POST', body: { card_id: cardId, actor_name: state.actor, source: 'ui' } });
        if (data?.card && applyArchivedCardPatch(data.card)) return;
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
      syncBoardControlSettingsForm();
      els.boardSettingsModal.classList.add('is-open');
    }

    function currentBoardControlSettings() {
      const snapshotSettings = state.snapshot?.settings?.ai_board_control && typeof state.snapshot.settings.ai_board_control === 'object'
        ? state.snapshot.settings.ai_board_control
        : {};
      return {
        enabled: Boolean(snapshotSettings.enabled),
        interval_minutes: Math.max(5, Math.min(240, Number(snapshotSettings.interval_minutes || 20) || 20)),
        cooldown_minutes: Math.max(5, Math.min(1440, Number(snapshotSettings.cooldown_minutes || 60) || 60)),
      };
    }

    function boardControlEntryExposure() {
      const payload = state.agentStatusPayload && typeof state.agentStatusPayload === 'object' ? state.agentStatusPayload : {};
      const exposure = payload?.ai_remodel?.effective_mode?.entry_exposure?.future_board_control_toggle;
      return exposure && typeof exposure === 'object' ? exposure : {};
    }

    function syncBoardControlSettingsForm() {
      const settings = currentBoardControlSettings();
      const exposure = boardControlEntryExposure();
      const visible = String(exposure.exposure_state || '').trim().toLowerCase() !== 'hidden';
      if (els.boardControlSettingsRow) els.boardControlSettingsRow.classList.toggle('hidden', !visible);
      if (els.boardControlToggle) els.boardControlToggle.checked = Boolean(settings.enabled);
      if (els.boardControlIntervalInput) els.boardControlIntervalInput.value = String(settings.interval_minutes);
      if (els.boardControlCooldownInput) els.boardControlCooldownInput.value = String(settings.cooldown_minutes);
    }

    function readBoardControlSettingsForm() {
      return {
        enabled: Boolean(els.boardControlToggle?.checked),
        interval_minutes: Math.max(5, Math.min(240, Number(els.boardControlIntervalInput?.value || 20) || 20)),
        cooldown_minutes: Math.max(5, Math.min(1440, Number(els.boardControlCooldownInput?.value || 60) || 60)),
      };
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
      const label = window.prompt('Название столбца');
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

    function cashboxFormatMinorAmount(value) {
      const amount = Number(value || 0);
      const sign = amount < 0 ? '-' : '';
      const absolute = Math.abs(amount) / 100;
      return sign + absolute.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' ?';
    }

    function activeCashboxStatistics() {
      return state.activeCashbox?.statistics || {
        transactions_total: 0,
        income_total_minor: 0,
        expense_total_minor: 0,
        balance_minor: 0,
        balance_display: cashboxFormatMinorAmount(0),
        balance_sign: 'positive',
      };
    }

    function filteredCashboxTransactions() {
      return Array.isArray(state.activeCashbox?.transactions) ? state.activeCashbox.transactions : [];
    }

    function buildCashboxStatistics(transactions) {
      const items = Array.isArray(transactions) ? transactions : [];
      let incomeMinor = 0;
      let expenseMinor = 0;
      let lastTransactionAt = '';
      items.forEach((item) => {
        const amountMinor = Number(item?.amount_minor || 0);
        if (item?.direction === 'expense') expenseMinor += amountMinor;
        else incomeMinor += amountMinor;
        if (item?.created_at && (!lastTransactionAt || String(item.created_at) > lastTransactionAt)) {
          lastTransactionAt = String(item.created_at);
        }
      });
      const balanceMinor = incomeMinor - expenseMinor;
      return {
        transactions_total: items.length,
        income_total_minor: incomeMinor,
        expense_total_minor: expenseMinor,
        balance_minor: balanceMinor,
        balance_display: cashboxFormatMinorAmount(balanceMinor),
        income_total_display: cashboxFormatMinorAmount(incomeMinor),
        expense_total_display: cashboxFormatMinorAmount(expenseMinor),
        last_transaction_at: lastTransactionAt,
      };
    }

    function cashboxTransactionSourceLabel(item) {
      const note = String(item?.note || '').trim();
      if (/^перемещение\b/i.test(note)) return 'перемещение';
      if (/заказ-наряд\\s*№/i.test(note)) return 'заказ-наряд';
      const source = String(item?.source || '').trim().toLowerCase();
      if (source === 'ui') return 'ручное';
      if (source === 'mcp') return 'mcp';
      return source ? source : 'система';
    }

    function syncCashboxFiltersUi() {
      return;
    }

    function syncCashboxInList(cashbox) {
      if (!cashbox?.id) return;
      const nextItems = (state.cashboxes || []).slice();
      const index = nextItems.findIndex((item) => item.id === cashbox.id);
      if (index >= 0) nextItems[index] = cashbox;
      else nextItems.push(cashbox);
      nextItems.sort((left, right) => String(left?.name || '').localeCompare(String(right?.name || ''), 'ru', { sensitivity: 'base' }));
      state.cashboxes = nextItems;
    }

    function renderCashboxesList() {
      const items = Array.isArray(state.cashboxes) ? state.cashboxes : [];
      els.cashboxesList.innerHTML = items.length ? items.map((item) => {
        const stats = item?.statistics || {};
        const balanceMinor = Number(stats?.balance_minor || 0);
        const activeClass = item.id === state.activeCashboxId ? ' is-active' : '';
        return '<button class="cashbox-row' + activeClass + '" type="button" data-cashbox-id="' + escapeHtml(item.id) + '">'
          + '<div class="cashbox-row__head">'
          + '<div class="cashbox-row__name">' + escapeHtml(item.name || '—') + '</div>'
          + '<div class="cashbox-row__balance" data-balance-sign="' + escapeHtml(balanceMinor < 0 ? 'negative' : 'positive') + '">' + escapeHtml(stats?.balance_display || cashboxFormatMinorAmount(balanceMinor)) + '</div>'
          + '</div>'
          + '</button>';
      }).join('') : '<div class="cashboxes-empty">КАСС ПОКА НЕТ.</div>';
    }

    function renderCashboxStats() {
      const stats = buildCashboxStatistics(filteredCashboxTransactions());
      const balanceMinor = Number(stats.balance_minor || 0);
      els.cashboxStats.innerHTML = [
        { label: 'Баланс', value: stats.balance_display || cashboxFormatMinorAmount(balanceMinor), sign: balanceMinor < 0 ? 'negative' : 'positive' },
        { label: 'Поступления', value: stats.income_total_display || cashboxFormatMinorAmount(stats.income_total_minor || 0), sign: 'positive' },
        { label: 'Списания', value: stats.expense_total_display || cashboxFormatMinorAmount(stats.expense_total_minor || 0), sign: 'positive' },
      ].map((item) => '<div class="cashbox-stat-grid"><div class="cashbox-stat-grid__label">' + escapeHtml(item.label) + '</div><div class="cashbox-stat-grid__value" data-balance-sign="' + escapeHtml(item.sign) + '">' + escapeHtml(item.value) + '</div></div>').join('');
    }

    function renderCashboxTransactions() {
      const transactions = filteredCashboxTransactions();
      els.cashboxTransactions.innerHTML = transactions.length ? transactions.map((item) => {
        const direction = item?.direction === 'expense' ? 'expense' : 'income';
        const note = String(item?.note || '').trim() || 'Без комментария';
        const actor = String(item?.actor_name || '').trim() || '—';
        const sourceLabel = cashboxTransactionSourceLabel(item);
        return '<div class="cashbox-transaction">'
          + '<div class="cashbox-transaction__badge" data-direction="' + escapeHtml(direction) + '">' + escapeHtml(direction === 'expense' ? 'списание' : 'поступление') + '</div>'
          + '<div class="cashbox-transaction__body"><div class="cashbox-transaction__summary"><div class="cashbox-transaction__note">' + escapeHtml(note) + '</div><div class="cashbox-transaction__context">' + escapeHtml(sourceLabel) + '</div></div><div class="cashbox-transaction__meta">' + escapeHtml(formatDate(item?.created_at)) + ' | ' + escapeHtml(actor) + '</div></div>'
          + '<div class="cashbox-transaction__amount" data-direction="' + escapeHtml(direction) + '">' + escapeHtml(direction === 'expense' ? '-' : '+') + escapeHtml(item?.amount_display || cashboxFormatMinorAmount(item?.amount_minor || 0)) + '</div>'
          + '</div>';
      }).join('') : '<div class="cashboxes-empty">ПО ФИЛЬТРУ НИЧЕГО НЕ НАЙДЕНО.</div>';
    }

    function renderCashboxDetail() {
      const cashbox = state.activeCashbox?.cashbox || null;
      if (!cashbox) {
        els.cashboxDetailTitle.textContent = 'КАССА НЕ ВЫБРАНА';
        els.cashboxDetailMeta.textContent = '';
        els.cashboxDeleteButton.disabled = true;
        els.cashboxIncomeButton.disabled = true;
        els.cashboxTransferButton.disabled = true;
        els.cashboxExpenseButton.disabled = true;
        syncCashboxFiltersUi();
        els.cashboxStats.innerHTML = '';
        els.cashboxTransactions.innerHTML = '<div class="cashboxes-empty">НЕТ ДАННЫХ.</div>';
        return;
      }
      const stats = activeCashboxStatistics();
      const canDelete = Number(stats.transactions_total || 0) === 0;
      els.cashboxDetailTitle.textContent = cashbox.name || 'КАССА';
      els.cashboxDetailMeta.textContent = '';
      els.cashboxDeleteButton.disabled = !canDelete;
      els.cashboxIncomeButton.disabled = false;
      els.cashboxTransferButton.disabled = (Array.isArray(state.cashboxes) ? state.cashboxes.length : 0) < 2;
      els.cashboxExpenseButton.disabled = false;
      syncCashboxFiltersUi();
      renderCashboxStats();
      renderCashboxTransactions();
    }

    async function loadCashboxDetail(cashboxId, { openModal = false } = {}) {
      const normalizedId = String(cashboxId || '').trim();
      if (!normalizedId) return null;
      try {
        const data = await api('/api/get_cashbox?cashbox_id=' + encodeURIComponent(normalizedId) + '&transaction_limit=500');
        state.activeCashboxId = data?.cashbox?.id || normalizedId;
        state.activeCashbox = {
          cashbox: data?.cashbox || null,
          transactions: Array.isArray(data?.transactions) ? data.transactions : [],
          meta: data?.meta || {},
          statistics: data?.cashbox?.statistics || {},
        };
        if (data?.cashbox) syncCashboxInList(data.cashbox);
        renderCashboxesList();
        renderCashboxDetail();
        maybeOpenModal(els.cashboxesModal, openModal);
        return data;
      } catch (error) {
        els.cashboxDetailTitle.textContent = 'ОШИБКА ЗАГРУЗКИ';
        els.cashboxDetailMeta.textContent = error.message;
        els.cashboxTransactions.innerHTML = '<div class="cashboxes-empty">' + escapeHtml(error.message) + '</div>';
        maybeOpenModal(els.cashboxesModal, openModal);
        setStatus(error.message, true);
        return null;
      }
    }

    async function loadCashboxes(openModal = false) {
      try {
        const data = await api('/api/list_cashboxes?limit=200');
        state.cashboxes = Array.isArray(data?.cashboxes) ? data.cashboxes : [];
        const total = Number(data?.meta?.total || state.cashboxes.length);
        els.cashboxCreateButton.disabled = total >= 6;
        els.cashboxCreateButton.title = '';
        const nextId = state.cashboxes.some((item) => item.id === state.activeCashboxId)
          ? state.activeCashboxId
          : (state.cashboxes[0]?.id || '');
        renderCashboxesList();
        if (nextId) {
          await loadCashboxDetail(nextId, { openModal });
          return;
        }
        state.activeCashboxId = '';
        state.activeCashbox = null;
        renderCashboxDetail();
        maybeOpenModal(els.cashboxesModal, openModal);
      } catch (error) {
        els.cashboxesList.innerHTML = '<div class="cashboxes-empty">' + escapeHtml(error.message) + '</div>';
        state.activeCashboxId = '';
        state.activeCashbox = null;
        renderCashboxDetail();
        maybeOpenModal(els.cashboxesModal, openModal);
        setStatus(error.message, true);
      }
    }

    function openCashboxesModal() {
      loadCashboxes(true);
    }

    async function createCashbox() {
      if (Array.isArray(state.cashboxes) && state.cashboxes.length >= 6) {
        setStatus('МАКСИМУМ 6 КАСС.', true);
        return;
      }
      const name = String(window.prompt('Название кассы') || '').trim();
      if (!name) {
        return;
      }
      try {
        els.cashboxCreateButton.disabled = true;
        const data = await api('/api/create_cashbox', {
          method: 'POST',
          body: { name, actor_name: state.actor, source: 'ui' },
        });
        if (data?.cashbox?.id) state.activeCashboxId = data.cashbox.id;
        await loadCashboxes(true);
        setStatus('КАССА СОЗДАНА.', false);
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        els.cashboxCreateButton.disabled = false;
      }
    }

    async function deleteActiveCashbox() {
      const cashbox = state.activeCashbox?.cashbox || null;
      if (!cashbox?.id) return;
      if (!window.confirm('Удалить кассу "' + String(cashbox.name || '').trim() + '"?')) return;
      try {
        els.cashboxDeleteButton.disabled = true;
        await api('/api/delete_cashbox', {
          method: 'POST',
          body: { cashbox_id: cashbox.id, actor_name: state.actor, source: 'ui' },
        });
        state.activeCashboxId = '';
        state.activeCashbox = null;
        await loadCashboxes(true);
        setStatus('КАССА УДАЛЕНА.', false);
      } catch (error) {
        const message = String(error?.message || '').trim();
        if (message) window.alert(message);
        setStatus(error.message, true);
      } finally {
        els.cashboxDeleteButton.disabled = false;
      }
    }

    async function createCashboxTransfer() {
      const sourceCashbox = state.activeCashbox?.cashbox || null;
      if (!sourceCashbox?.id) {
        setStatus('СНАЧАЛА ВЫБЕРИТЕ КАССУ.', true);
        return;
      }
      const availableCashboxes = (Array.isArray(state.cashboxes) ? state.cashboxes : []).filter((item) => item.id !== sourceCashbox.id);
      if (!availableCashboxes.length) {
        setStatus('НЕТ ДРУГОЙ КАССЫ ДЛЯ ПЕРЕМЕЩЕНИЯ.', true);
        return;
      }
      state.cashboxTransferDraft = {
        sourceId: sourceCashbox.id,
        targetId: availableCashboxes[0]?.id || '',
        amount: '',
        note: '',
      };
      if (els.cashboxTransferAmountInput) els.cashboxTransferAmountInput.value = '';
      if (els.cashboxTransferNoteInput) els.cashboxTransferNoteInput.value = '';
      renderCashboxTransferModal();
      maybeOpenModal(els.cashboxTransferModal, true);
    }

    function renderCashboxTransferModal() {
      const sourceId = String(state.cashboxTransferDraft?.sourceId || state.activeCashbox?.cashbox?.id || '').trim();
      const sourceCashbox = (Array.isArray(state.cashboxes) ? state.cashboxes : []).find((item) => item.id === sourceId) || state.activeCashbox?.cashbox || null;
      const availableCashboxes = (Array.isArray(state.cashboxes) ? state.cashboxes : []).filter((item) => item.id !== sourceId);
      els.cashboxTransferSourceName.textContent = sourceCashbox?.name || 'КАССА НЕ ВЫБРАНА';
      els.cashboxTransferTargets.innerHTML = availableCashboxes.length ? availableCashboxes.map((item) => {
        const activeClass = item.id === state.cashboxTransferDraft.targetId ? ' is-active' : '';
        return '<button class="cashbox-transfer-target' + activeClass + '" type="button" data-cashbox-transfer-target="' + escapeHtml(item.id) + '">'
          + '<div class="cashbox-transfer-target__name">' + escapeHtml(item.name || '—') + '</div>'
          + '</button>';
      }).join('') : '<div class="cashboxes-empty">НЕТ ДРУГИХ КАСС.</div>';
      const selectedTarget = availableCashboxes.find((item) => item.id === state.cashboxTransferDraft.targetId) || availableCashboxes[0] || null;
      if (selectedTarget && selectedTarget.id !== state.cashboxTransferDraft.targetId) {
        state.cashboxTransferDraft.targetId = selectedTarget.id;
      }
      if (els.cashboxTransferConfirmButton) {
        const hasAmount = String(els.cashboxTransferAmountInput?.value || state.cashboxTransferDraft.amount || '').trim().length > 0;
        els.cashboxTransferConfirmButton.disabled = !selectedTarget || !sourceCashbox || !hasAmount;
      }
    }

    function setCashboxTransferTarget(cashboxId) {
      const requestedId = String(cashboxId || '').trim();
      const sourceId = String(state.cashboxTransferDraft?.sourceId || '').trim();
      if (!requestedId || requestedId === sourceId) return;
      state.cashboxTransferDraft.targetId = requestedId;
      renderCashboxTransferModal();
    }

    async function submitCashboxTransfer() {
      const sourceCashbox = (Array.isArray(state.cashboxes) ? state.cashboxes : []).find((item) => item.id === state.cashboxTransferDraft.sourceId) || null;
      const targetCashbox = (Array.isArray(state.cashboxes) ? state.cashboxes : []).find((item) => item.id === state.cashboxTransferDraft.targetId) || null;
      if (!sourceCashbox?.id) {
        setStatus('СНАЧАЛА ВЫБЕРИТЕ КАССУ.', true);
        return;
      }
      if (!targetCashbox?.id || targetCashbox.id === sourceCashbox.id) {
        setStatus('УКАЖИТЕ КАССУ ДЛЯ ПЕРЕМЕЩЕНИЯ.', true);
        return;
      }
      const amount = String(els.cashboxTransferAmountInput?.value || '').trim();
      if (!amount) {
        setStatus('УКАЖИТЕ СУММУ.', true);
        return;
      }
      try {
        els.cashboxTransferConfirmButton.disabled = true;
        els.cashboxTransferButton.disabled = true;
        await api('/api/create_cashbox_transfer', {
          method: 'POST',
          body: {
            from_cashbox_id: sourceCashbox.id,
            to_cashbox_id: targetCashbox.id,
            amount,
            note: String(els.cashboxTransferNoteInput?.value || '').trim(),
            actor_name: state.actor,
            source: 'ui',
          },
        });
        if (els.cashboxTransferAmountInput) els.cashboxTransferAmountInput.value = '';
        if (els.cashboxTransferNoteInput) els.cashboxTransferNoteInput.value = '';
        els.cashboxTransferModal.classList.remove('is-open');
        await loadCashboxes(true);
        setStatus('ПЕРЕМЕЩЕНИЕ СОХРАНЕНО.', false);
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        els.cashboxTransferConfirmButton.disabled = false;
        els.cashboxTransferButton.disabled = false;
      }
    }

    function handleCashboxTransferAmountInput() {
      state.cashboxTransferDraft.amount = String(els.cashboxTransferAmountInput?.value || '').trim();
      renderCashboxTransferModal();
    }

    function handleCashboxTransferNoteInput() {
      state.cashboxTransferDraft.note = String(els.cashboxTransferNoteInput?.value || '');
    }

    function handleCashboxTransferTargetsClick(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const button = target.closest('[data-cashbox-transfer-target]');
      if (!button) return;
      setCashboxTransferTarget(button.getAttribute('data-cashbox-transfer-target'));
    }

    async function createCashboxTransaction(direction) {
      const cashbox = state.activeCashbox?.cashbox || null;
      if (!cashbox?.id) {
        setStatus('СНАЧАЛА ВЫБЕРИТЕ КАССУ.', true);
        return;
      }
      const amount = String(els.cashboxAmountInput.value || '').trim();
      if (!amount) {
        setStatus('УКАЖИТЕ СУММУ.', true);
        return;
      }
      try {
        els.cashboxIncomeButton.disabled = true;
        els.cashboxExpenseButton.disabled = true;
        await api('/api/create_cash_transaction', {
          method: 'POST',
          body: {
            cashbox_id: cashbox.id,
            direction: direction === 'expense' ? 'expense' : 'income',
            amount,
            note: String(els.cashboxNoteInput.value || '').trim(),
            actor_name: state.actor,
            source: 'ui',
          },
        });
        els.cashboxAmountInput.value = '';
        els.cashboxNoteInput.value = '';
        await loadCashboxDetail(cashbox.id, { openModal: true });
        setStatus(direction === 'expense' ? 'СПИСАНИЕ СОХРАНЕНО.' : 'ПОСТУПЛЕНИЕ СОХРАНЕНО.', false);
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        els.cashboxIncomeButton.disabled = false;
        els.cashboxExpenseButton.disabled = false;
      }
    }

    async function handleCashboxesListClick(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const row = target.closest('[data-cashbox-id]');
      if (!row) return;
      await loadCashboxDetail(row.dataset.cashboxId, { openModal: true });
    }

    async function handleCashboxesListKeydown(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const row = target.closest('[data-cashbox-id]');
      if (!row) return;
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      await loadCashboxDetail(row.dataset.cashboxId, { openModal: true });
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
        const data = await api('/api/archive_card', { method: 'POST', body: { card_id: state.editingId, actor_name: state.actor, source: 'ui' } });
        closeCardModal();
        if (data?.card && applyArchivedCardPatch(data.card)) return;
        await refreshSnapshot(true);
      } catch (error) {
        const message = String(error?.message || '').trim();
        if (message.includes('открыт заказ-наряд')) window.alert(message);
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
      return;
    }

    function handleRepairOrderModalOverlayClick(event) {
      return;
    }

    function handleRepairOrderPaymentsModalOverlayClick(event) {
      return;
    }

    function handleAgentModalOverlayClick(event) {
      return;
    }

    function handleEmployeesModalOverlayClick(event) {
      return;
    }

    function handleAgentQuickActionClick(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const openButton = target.closest('[data-agent-open]');
      if (openButton instanceof HTMLElement) {
        if (String(openButton.dataset.agentOpen || '').trim() === 'tasks') openAgentTasksModal();
        return;
      }
      const button = target.closest('[data-agent-prompt]');
      if (!(button instanceof HTMLElement)) return;
      const prompt = String(button.dataset.agentPrompt || '').trim();
      const template = String(button.dataset.agentTemplate || '').trim();
      if (!prompt || !els.agentTaskInput) return;
      els.agentTaskInput.value = prompt;
      if (template) {
        els.agentTaskInput.dataset.agentPromptTemplate = template;
      } else {
        delete els.agentTaskInput.dataset.agentPromptTemplate;
      }
      syncAgentTaskInputHeight();
      els.agentTaskInput.focus();
    }

    function handleAgentRunSelection(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const row = target.closest('[data-agent-task-id]');
      if (!(row instanceof HTMLElement)) return;
      const taskId = String(row.dataset.agentTaskId || '').trim();
      if (!taskId) return;
      state.agentTaskId = taskId;
      refreshAgentModalState();
    }

    function handleOperatorProfileModalOverlayClick(event) {
      return;
    }

    function handleOperatorAdminModalOverlayClick(event) {
      return;
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
        let data = null;
        if (payload.sticky_id) {
          data = await api('/api/update_sticky', { method: 'POST', body: { sticky_id: payload.sticky_id, text: payload.text, deadline: payload.deadline, actor_name: state.actor, source: 'ui' } });
        } else {
          data = await api('/api/create_sticky', { method: 'POST', body: { text: payload.text, x: payload.x, y: payload.y, deadline: payload.deadline, actor_name: state.actor, source: 'ui' } });
        }
        closeStickyModal();
        if (applyStickySnapshot(data?.stickies || [])) {
          setStatus('СТИКЕР СОХРАНЕН.', false);
          return;
        }
        await refreshSnapshot(true);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function removeSticky(stickyId) {
      try {
        const data = await api('/api/delete_sticky', { method: 'POST', body: { sticky_id: stickyId, actor_name: state.actor, source: 'ui' } });
        if (applyStickySnapshot(data?.stickies || [])) {
          setStatus('СТИКЕР УДАЛЕН.', false);
          return;
        }
        await refreshSnapshot(true);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function saveBoardScale() {
      const scale = normalizeBoardScale(Number(els.boardScaleInput.value) / 100);
      applyBoardScale(scale, { syncInput: true });
      persistStoredBoardScale(scale);
      const aiBoardControl = readBoardControlSettingsForm();
      const data = await api('/api/update_board_settings', {
        method: 'POST',
        body: {
          board_scale: scale,
          ai_board_control: aiBoardControl,
          actor_name: state.actor,
          source: 'ui',
        },
      });
      if (data?.settings && typeof data.settings === 'object') {
        state.snapshot = state.snapshot && typeof state.snapshot === 'object' ? state.snapshot : {};
        state.snapshot.settings = data.settings;
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
        const data = await api('/api/move_sticky', { method: 'POST', body: { sticky_id: drag.stickyId, x: nextX, y: nextY, actor_name: state.actor, source: 'ui' } });
        if (applyStickySnapshot(data?.stickies || [])) return;
        await refreshSnapshot(true);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function uploadProvidedFiles(files) {
      const selectedFiles = Array.from(files || []).filter(Boolean);
      if (!selectedFiles.length) return;
      if (!requireSavedCardForFiles({ syncDropzone: true })) return;
      try {
        const normalizedFiles = selectedFiles.map((file) => normalizeUploadableAttachmentFile(file));
        for (const file of normalizedFiles) {
          const buffer = await file.arrayBuffer();
          const base64 = arrayBufferToBase64(buffer);
          await api('/api/add_card_attachment', { method: 'POST', body: { card_id: state.editingId, actor_name: state.actor, source: 'ui', file_name: file.name, mime_type: normalizeAttachmentMimeType(file.type) || attachmentMimeTypeFromExtension(attachmentExtension(file.name)) || 'application/octet-stream', content_base64: base64 } });
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
      if (target.matches('[data-repair-order-cell="executor_id"]')) {
        const row = target.closest('tr[data-repair-order-row]');
        if (row instanceof HTMLElement) {
          row.dataset.repairOrderSalaryMode = '';
          row.dataset.repairOrderBaseSalary = '';
          row.dataset.repairOrderWorkPercent = '';
          row.dataset.repairOrderSalaryAmount = '';
          row.dataset.repairOrderSalaryAccruedAt = '';
        }
      }
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
        setStatus('В БУФЕРЕ НЕТ ФАЙЛА ИЛИ ТЕКСТА ДЛЯ ВЛОЖЕНИЯ.', true);
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

    function handleBoardColumnDragStart(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      if (target.closest('.card')) return;
      if (target.closest('button, input, textarea, select, a, label')) return;
      const column = target.closest('.column');
      if (!(column instanceof HTMLElement)) return;
      state.boardDragColumnId = column.dataset.columnId || '';
      column.classList.add('is-column-dragging');
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('application/x-kanban-column', state.boardDragColumnId);
      }
    }

    function handleBoardCardDragOver(event) {
      const draggedCardId = state.boardDragCardId || event.dataTransfer?.getData('text/plain') || '';
      if (!draggedCardId) return;
      event.preventDefault();
      updateBoardDragAutoScroll(event.clientX, event.clientY);
      const rawTarget = event.target;
      const target = rawTarget instanceof Element
        ? rawTarget
        : (rawTarget instanceof Node ? rawTarget.parentElement : null);
      if (!(target instanceof Element)) return;
      const column = target.closest('.column');
      if (!column) {
        clearCardDropState();
        return;
      }
      const beforeCardId = resolveDropBeforeCardId(column, event.clientY, draggedCardId);
      updateCardDropState(column, beforeCardId);
    }

    function handleBoardColumnDragOver(event) {
      const draggedColumnId = state.boardDragColumnId || event.dataTransfer?.getData('application/x-kanban-column') || '';
      if (!draggedColumnId) return;
      event.preventDefault();
      updateBoardDragAutoScroll(event.clientX, event.clientY);
      const target = event.target instanceof Element
        ? event.target
        : (event.target instanceof Node ? event.target.parentElement : null);
      if (!(target instanceof Element)) return;
      const column = target.closest('.column');
      if (!(column instanceof HTMLElement)) {
        clearColumnDropState();
        return;
      }
      const hoveredColumnId = String(column.dataset.columnId || '').trim();
      if (!hoveredColumnId || hoveredColumnId === draggedColumnId) {
        clearColumnDropState();
        return;
      }
      state.boardDropBeforeColumnId = resolveDropBeforeColumnId(column, event.clientX, draggedColumnId);
      document.querySelectorAll('.column.is-column-drop-target').forEach((item) => item.classList.remove('is-column-drop-target'));
      column.classList.add('is-column-drop-target');
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

    function handleBoardColumnDragLeave(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const column = target.closest('.column');
      if (!column) return;
      const relatedTarget = event.relatedTarget;
      if (relatedTarget instanceof HTMLElement && column.contains(relatedTarget)) return;
      clearColumnDropState();
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

    async function handleBoardColumnDrop(event) {
      const draggedColumnId = state.boardDragColumnId || event.dataTransfer?.getData('application/x-kanban-column') || '';
      if (!draggedColumnId) return;
      const target = event.target;
      if (!(target instanceof HTMLElement)) {
        finishColumnDrag();
        return;
      }
      const column = target.closest('.column');
      if (!(column instanceof HTMLElement)) {
        finishColumnDrag();
        return;
      }
      event.preventDefault();
      const hoveredColumnId = String(column.dataset.columnId || '').trim();
      const beforeColumnId = state.boardDropBeforeColumnId || resolveDropBeforeColumnId(column, event.clientX, draggedColumnId);
      if (hoveredColumnId && hoveredColumnId !== draggedColumnId) {
        await moveColumn(draggedColumnId, beforeColumnId);
      } else {
        finishColumnDrag();
      }
    }

    document.addEventListener('click', async (event) => {
      const rawTarget = event.target;
      const target = rawTarget instanceof Element
        ? rawTarget
        : (rawTarget instanceof Node ? rawTarget.parentElement : null);
      if (!(target instanceof Element)) return;
      const closeTrigger = target.closest('[data-close]');
      if (closeTrigger instanceof HTMLElement) closeNamedModal(closeTrigger.dataset.close);
      const tabTrigger = target.closest('[data-tab]');
      if (tabTrigger instanceof HTMLElement) setTab(tabTrigger.dataset.tab);
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
      const createInTrigger = target.closest('[data-create-in]');
      if (createInTrigger instanceof HTMLElement) openNewCardInColumn(createInTrigger.dataset.createIn);
      if (await handleAuxiliaryBoardClick(target, event)) return;
      if (await handleCardWorkspaceClick(target)) return;
    });

    document.addEventListener('pointerover', handleCardSeenPointerOver);
    document.addEventListener('pointerout', handleCardSeenPointerOut);
    document.addEventListener('dragstart', handleBoardColumnDragStart);
    document.addEventListener('dragstart', handleBoardCardDragStart);
    document.addEventListener('dragover', handleBoardColumnDragOver);
    document.addEventListener('dragover', handleBoardCardDragOver);
    document.addEventListener('dragleave', handleBoardColumnDragLeave);
    document.addEventListener('dragleave', handleBoardCardDragLeave);
    document.addEventListener('drop', handleBoardColumnDrop);
    document.addEventListener('drop', handleBoardCardDrop);
    document.addEventListener('dragend', finishBoardDrag);
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
    remountElement('cashboxesButton');
    remountElement('employeesButton');
    remountElement('cashboxCreateButton');
    remountElement('cashboxDeleteButton');
    remountElement('cashboxIncomeButton');
    remountElement('cashboxTransferButton');
    remountElement('cashboxExpenseButton');
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
    els.cashboxesButton.addEventListener('click', openCashboxesModal);
    els.employeesButton.addEventListener('click', openEmployeesModal);
    els.repairOrdersOpenTab.addEventListener('click', () => setRepairOrdersFilter('open'));
    els.repairOrdersClosedTab.addEventListener('click', () => setRepairOrdersFilter('closed'));
    els.repairOrdersSearchInput.addEventListener('input', handleRepairOrdersSearchInput);
    els.repairOrdersSortBy.addEventListener('change', handleRepairOrdersSortChange);
    els.repairOrdersSortDir.addEventListener('change', handleRepairOrdersSortChange);
    els.cashboxCreateButton.addEventListener('click', createCashbox);
    els.cashboxDeleteButton.addEventListener('click', deleteActiveCashbox);
    els.cashboxIncomeButton.addEventListener('click', () => createCashboxTransaction('income'));
    els.cashboxTransferButton.addEventListener('click', createCashboxTransfer);
    els.cashboxExpenseButton.addEventListener('click', () => createCashboxTransaction('expense'));
    els.cashboxTransferTargets.addEventListener('click', handleCashboxTransferTargetsClick);
    els.cashboxTransferConfirmButton.addEventListener('click', submitCashboxTransfer);
    els.cashboxTransferAmountInput.addEventListener('input', handleCashboxTransferAmountInput);
    els.cashboxTransferNoteInput.addEventListener('input', handleCashboxTransferNoteInput);
    els.cashboxesList.addEventListener('click', handleCashboxesListClick);
    els.cashboxesList.addEventListener('keydown', handleCashboxesListKeydown);
    els.cashboxAmountInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        createCashboxTransaction('income');
      }
    });
    els.gptWallButton.addEventListener('click', openGptWallModal);
    els.gptWallBoardTab.addEventListener('click', () => setGptWallView('board_content'));
    els.gptWallEventsTab.addEventListener('click', () => setGptWallView('event_log'));
    els.gptWallRefresh.addEventListener('click', refreshGptWallView);
    els.boardScaleInput.addEventListener('input', handleBoardScaleInput);
    els.boardScaleInput.addEventListener('change', persistBoardScaleChange);
    els.boardScaleReset.addEventListener('click', resetBoardScaleToDefault);
    els.boardControlToggle?.addEventListener('change', persistBoardScaleChange);
    els.boardControlIntervalInput?.addEventListener('change', persistBoardScaleChange);
    els.boardControlCooldownInput?.addEventListener('change', persistBoardScaleChange);
    els.columnButton.addEventListener('click', createColumnFromTopbar);
    els.cardButton.addEventListener('click', openDefaultNewCard);
    els.signalDaysIncrementButton.addEventListener('click', () => adjustSignalPart('days', 1));
    els.signalDaysDecrementButton.addEventListener('click', () => adjustSignalPart('days', -1));
    els.signalHoursIncrementButton.addEventListener('click', () => adjustSignalPart('hours', 1));
    els.signalHoursDecrementButton.addEventListener('click', () => adjustSignalPart('hours', -1));
    [els.signalDays, els.signalHours].forEach((input) => {
      input.addEventListener('input', renderSignalPreview);
      input.addEventListener('change', renderSignalPreview);
    });
    els.tagAddButton.addEventListener('click', addDraftTag);
    els.tagInput.addEventListener('keydown', handleTagInputKeydown);
    configureVehicleAutofillUi();
    els.cardDescription.addEventListener('input', syncCardDescriptionHeight);
    els.cardAgentButton?.addEventListener('click', runCardCleanup);
    els.vehicleAutofillButton.addEventListener('click', autofillVehicleProfile);
    els.repairOrderAddWorkRowButton.addEventListener('click', (event) => addRepairOrderRowFromButton('works', event));
    els.repairOrderAddMaterialRowButton.addEventListener('click', (event) => addRepairOrderRowFromButton('materials', event));
    els.repairOrderModal.addEventListener('input', handleRepairOrderModalInput);
    els.repairOrderModal.addEventListener('change', handleRepairOrderModalInput);
    els.repairOrderTagAddButton.addEventListener('click', addRepairOrderTag);
    els.repairOrderTagInput.addEventListener('keydown', handleRepairOrderTagInputKeydown);
    els.repairOrderButton.addEventListener('click', openRepairOrderModal);
    els.repairOrderAutofillButton.addEventListener('click', autofillRepairOrder);
    els.repairOrderCloseButton.addEventListener('click', toggleRepairOrderStatus);
    els.repairOrderSaveButton.addEventListener('click', saveRepairOrderDraft);
    els.repairOrderPaymentsButton.addEventListener('click', openRepairOrderPaymentsModal);
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

    function boardCardDescription(card) {
      return String(card?.description_preview || card?.description || 'Описание не указано');
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
      return '<article class="card" style="' + heatStyle + '" draggable="true" data-card-id="' + escapeHtml(card.id) + '" data-indicator="' + escapeHtml(card.indicator) + '" data-status="' + escapeHtml(card.status) + '" data-blink="' + (card.is_blinking ? "true" : "false") + '" data-unread="' + (card.is_unread ? 'true' : 'false') + '" data-updated-unseen="' + (card.has_unseen_update ? 'true' : 'false') + '" data-deadline-bucket="' + escapeHtml(card.deadline_progress_bucket ?? 0) + '" data-deadline-step="' + escapeHtml(card.deadline_progress_step_percent ?? 0) + '">' + badgeHtml + headingHtml + '<div class="card__desc">' + escapeHtml(boardCardDescription(card)) + '</div><div class="card__signal"><span class="card__signal-label"><span class="lamp" data-indicator="' + escapeHtml(card.indicator) + '"></span><span>СИГН</span></span><span class="card__signal-value">' + durationToMarkup(card.remaining_seconds, false) + '</span></div><div class="card__tags">' + tagsHtml + '</div></article>';
    };

    function legacyCardHtmlBase(card) {
      const previewTags = (card.tags || []).slice(0, CARD_TAG_LIMIT);
      const extraTags = (card.tags || []).length - previewTags.length;
      const tagsHtml = previewTags.length
        ? previewTags.map((tag) => '<span class="tag">' + escapeHtml(tag) + '</span>').join('') + (extraTags > 0 ? '<span class="tag">+' + extraTags + '</span>' : '')
        : '<span class="tag tag--muted">БЕЗ МЕТОК</span>';
      const headingHtml = buildCardHeadingHtml(card);
      return '<article class="card" draggable="true" data-card-id="' + escapeHtml(card.id) + '" data-indicator="' + escapeHtml(card.indicator) + '" data-status="' + escapeHtml(card.status) + '" data-blink="' + (card.is_blinking ? 'true' : 'false') + '">' + headingHtml + '<div class="card__desc">' + escapeHtml(card.description || 'Описание не указано') + '</div><div class="card__signal"><span class="card__signal-label"><span class="lamp" data-indicator="' + escapeHtml(card.indicator) + '"></span><span>СИГН</span></span><span class="card__signal-value">' + durationToMarkup(card.remaining_seconds, false) + '</span></div><div class="card__tags">' + tagsHtml + '</div></article>';
    }

    function legacyRenderCardHtmlBase(card) {
      const normalizedTags = normalizeDraftTags(card.tag_items || card.tags || []);
      const previewTags = normalizedTags.slice(0, CARD_TAG_LIMIT);
      const extraTags = normalizedTags.length - previewTags.length;
      const tagsHtml = previewTags.length
        ? previewTags.map((tag) => '<span class="tag" data-tag-color="' + escapeHtml(tag.color) + '"><span class="tag__dot"></span>' + escapeHtml(tag.label) + '</span>').join('') + (extraTags > 0 ? '<span class="tag">+' + extraTags + '</span>' : '')
        : '<span class="tag tag--muted">БЕЗ МЕТОК</span>';
      const headingHtml = buildCardHeadingHtml(card);
      const unreadBadgeHtml = cardUnreadBadgeHtml(card);
      const heatStyle = '--deadline-heat-border:' + escapeHtml(card.deadline_heat_border_color || 'rgba(83, 191, 122, 0.34)') + ';--deadline-heat-ring:' + escapeHtml(card.deadline_heat_ring_color || 'rgba(83, 191, 122, 0.08)') + ';--deadline-heat-glow:' + escapeHtml(card.deadline_heat_glow_color || 'rgba(83, 191, 122, 0.04)') + ';';
      return '<article class="card" style="' + heatStyle + '" draggable="true" data-card-id="' + escapeHtml(card.id) + '" data-indicator="' + escapeHtml(card.indicator) + '" data-status="' + escapeHtml(card.status) + '" data-blink="' + (card.is_blinking ? "true" : "false") + '" data-unread="' + (card.is_unread ? 'true' : 'false') + '" data-deadline-bucket="' + escapeHtml(card.deadline_progress_bucket ?? 0) + '" data-deadline-step="' + escapeHtml(card.deadline_progress_step_percent ?? 0) + '">' + unreadBadgeHtml + headingHtml + '<div class="card__desc">' + escapeHtml(card.description || 'Описание не указано') + '</div><div class="card__signal"><span class="card__signal-label"><span class="lamp" data-indicator="' + escapeHtml(card.indicator) + '"></span><span>СИГН</span></span><span class="card__signal-value">' + durationToMarkup(card.remaining_seconds, false) + '</span></div><div class="card__tags">' + tagsHtml + '</div></article>';
    }

    function legacyCardHtmlShadow(card) {
      const previewTags = (card.tags || []).slice(0, CARD_TAG_LIMIT);
      const extraTags = (card.tags || []).length - previewTags.length;
      const tagsHtml = previewTags.length
        ? previewTags.map((tag) => '<span class="tag">' + escapeHtml(tag) + '</span>').join('') + (extraTags > 0 ? '<span class="tag">+' + extraTags + '</span>' : '')
        : '<span class="tag tag--muted">БЕЗ МЕТОК</span>';
      const headingHtml = buildCardHeadingHtml(card);
      return '<article class="card" draggable="true" data-card-id="' + escapeHtml(card.id) + '" data-indicator="' + escapeHtml(card.indicator) + '" data-status="' + escapeHtml(card.status) + '" data-blink="' + (card.is_blinking ? 'true' : 'false') + '">' + headingHtml + '<div class="card__desc">' + escapeHtml(card.description || 'Описание не указано') + '</div><div class="card__signal"><span class="card__signal-label"><span class="lamp" data-indicator="' + escapeHtml(card.indicator) + '"></span><span>СИГН</span></span><span class="card__signal-value">' + durationToMarkup(card.remaining_seconds, false) + '</span></div><div class="card__tags">' + tagsHtml + '</div></article>';
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
      return '<article class="card" style="' + heatStyle + '" draggable="true" data-card-id="' + escapeHtml(card.id) + '" data-indicator="' + escapeHtml(card.indicator) + '" data-status="' + escapeHtml(card.status) + '" data-blink="' + (card.is_blinking ? "true" : "false") + '" data-deadline-bucket="' + escapeHtml(card.deadline_progress_bucket ?? 0) + '" data-deadline-step="' + escapeHtml(card.deadline_progress_step_percent ?? 0) + '">' + headingHtml + '<div class="card__desc">' + escapeHtml(card.description || 'Описание не указано') + '</div><div class="card__signal"><span class="card__signal-label"><span class="lamp" data-indicator="' + escapeHtml(card.indicator) + '"></span><span>СИГН</span></span><span class="card__signal-value">' + durationToMarkup(card.remaining_seconds, false) + '</span></div><div class="card__tags">' + tagsHtml + '</div></article>';
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
    renderCashboxDetail();
    bindDirectCardModalCloseButtons();
    mountStatusLine();
    bootstrapOperatorSession();
    refreshSnapshot(true);
    document.addEventListener('visibilitychange', handleSnapshotVisibilityChange);
    startSnapshotPolling();
  </script>
</body>
</html>
""",
    ]
)


