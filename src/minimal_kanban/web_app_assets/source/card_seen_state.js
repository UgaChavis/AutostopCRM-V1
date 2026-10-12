    function applyCardSeenMarkers(card) {
      const currentCard = snapshotCardById(card?.id);
      if (!currentCard || String(currentCard.updated_at || '') !== String(card.updated_at || '')) return false;
      const markers = { is_unread: Boolean(card.is_unread), has_unseen_update: Boolean(card.has_unseen_update) };
      const nextCard = { ...currentCard, ...markers };
      if (Array.isArray(state.snapshot?.cards)) state.snapshot.cards = state.snapshot.cards.map((item) => item.id === card.id ? nextCard : item);
      if (Array.isArray(state.archiveCards)) state.archiveCards = state.archiveCards.map((item) => item.id === card.id ? nextCard : item);
      if (state.activeCard?.id === card.id && state.activeCard.updated_at === card.updated_at) {
        state.activeCard = { ...state.activeCard, ...markers };
      }
      if (state.mobileCard?.id === card.id && state.mobileCard.updated_at === card.updated_at) {
        state.mobileCard = { ...state.mobileCard, ...markers };
      }
      replaceBoardCardElement(nextCard);
      if (els.archiveModal?.classList.contains('is-open')) renderArchive();
      return true;
    }

    async function markCardSeen(cardId, { force = false } = {}) {
      const normalizedCardId = String(cardId || '').trim();
      if (!normalizedCardId) return;
      const viewerStateGeneration = state.viewerStateGeneration;
      const operatorSessionToken = state.operatorSessionToken;
      const isCurrent = () => viewerStateGeneration === state.viewerStateGeneration && operatorSessionToken === state.operatorSessionToken;
      if (state.cardSaveInFlight) {
        deferCardSeen(normalizedCardId, { force });
        return;
      }
      const currentCard = snapshotCardById(normalizedCardId) || (state.activeCard?.id === normalizedCardId ? state.activeCard : null);
      if (!force && currentCard && !currentCard.is_unread && !currentCard.has_unseen_update) return;
      if (state.unreadSeenInFlight.has(normalizedCardId)) return;
      if (!force) markCardSeenOptimistically(normalizedCardId);
      const suppression = state.cardSeenSuppressions.get(normalizedCardId);
      state.unreadSeenInFlight.add(normalizedCardId);
      try {
        const data = await cardPersistWrite('/api/mark_card_seen', { card_id: normalizedCardId, actor_name: state.actor, source: 'ui' });
        if (isCurrent() && data?.card && applyCardSeenMarkers(data.card)) {
          state.cardSeenSuppressions.delete(normalizedCardId);
          recordCardSeenSuppression(normalizedCardId, data.card.updated_at, { confirmed: true });
        }
      } catch (_) {
        if (!isCurrent()) return;
        if (state.cardSeenSuppressions.get(normalizedCardId) === suppression) state.cardSeenSuppressions.delete(normalizedCardId);
        try {
          // A lost response may still have committed. Reconcile personal markers
          // without replacing title, position, revision or the editor's draft.
          const readback = await api('/api/get_card?card_id=' + encodeURIComponent(normalizedCardId));
          if (isCurrent() && readback?.card && applyCardSeenMarkers(readback.card)
              && !readback.card.is_unread && !readback.card.has_unseen_update) {
            state.cardSeenSuppressions.delete(normalizedCardId);
            recordCardSeenSuppression(normalizedCardId, readback.card.updated_at, { confirmed: true });
          }
        } catch (_) {
          if (isCurrent() && suppression) applyCardSeenMarkers({ id: normalizedCardId, ...suppression });
        }
      } finally {
        if (isCurrent()) state.unreadSeenInFlight.delete(normalizedCardId);
      }
    }
