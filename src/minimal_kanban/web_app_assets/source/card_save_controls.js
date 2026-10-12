    // Remains in the startup asset: controls lock before the save module loads.
    function captureCardSaveWorkspace() {
      const generation = state.viewerStateGeneration;
      const session = state.operatorSessionToken;
      const editing = state.cardEditingGeneration || 0;
      return () => state.viewerStateGeneration === generation && state.operatorSessionToken === session
        && (state.cardEditingGeneration || 0) === editing;
    }

    function setCardSaveInteractionLocked(locked) {
      if (!els.cardModal) return;
      els.cardModal.setAttribute('aria-busy', locked ? 'true' : 'false');
      if (locked && !state.cardSaveControls) {
        state.cardSaveControls = Array.from(els.cardModal.querySelectorAll('input, textarea, select, button, [contenteditable]'))
          .map((element) => ({ element, disabled: element.disabled, editable: element.getAttribute('contenteditable') }));
        for (const { element, editable } of state.cardSaveControls) {
          if ('disabled' in element) element.disabled = true;
          if (editable !== null) element.setAttribute('contenteditable', 'false');
        }
        els.cardModal.inert = true;
      } else if (!locked && state.cardSaveControls) {
        for (const { element, disabled, editable } of state.cardSaveControls) {
          if ('disabled' in element) element.disabled = disabled;
          if (editable !== null) element.setAttribute('contenteditable', editable);
        }
        state.cardSaveControls = null;
        els.cardModal.inert = false;
      }
    }
