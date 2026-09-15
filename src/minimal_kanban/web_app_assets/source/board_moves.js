    function applyBoardColumnCardsPatch(nextCards, affectedColumnIds) {
      if (!Array.isArray(state.snapshot?.cards) || !Array.isArray(nextCards) || !Array.isArray(affectedColumnIds)) return false;
      const normalizedColumnIds = affectedColumnIds
        .map((value) => String(value || '').trim())
        .filter(Boolean);
      if (!normalizedColumnIds.length) return false;
      const targetColumns = new Set(normalizedColumnIds);
      const suppressedNextCards = applyCardSeenSuppressionsToCards(nextCards);
      const nextCardMap = new Map(suppressedNextCards.filter((card) => card?.id).map((card) => [card.id, card]));
      state.snapshot.cards = state.snapshot.cards
        .filter((card) => !targetColumns.has(String(card.column || '').trim()))
        .concat(suppressedNextCards);
      if (state.activeCard?.id) {
        const nextActiveCard = nextCardMap.get(state.activeCard.id);
        if (nextActiveCard && !state.activeCardIsFull) state.activeCard = nextActiveCard;
      }
      if (extraBoardColumnIsOpen()) {
        renderBoard();
        return true;
      }
      const cardsByColumn = buildBoardCardsByColumn(state.snapshot);
      let renderedAny = false;
      for (const columnId of normalizedColumnIds) {
        renderedAny = renderBoardColumnById(columnId, cardsByColumn) || renderedAny;
      }
      return renderedAny;
    }

    function applyBoardColumnOrderDelta(movedCard, affectedColumns, affectedColumnIds) {
      if (
        !movedCard?.id
        || !Array.isArray(state.snapshot?.cards)
        || !Array.isArray(affectedColumns)
        || !Array.isArray(affectedColumnIds)
      ) return false;
      const normalizedColumnIds = affectedColumnIds
        .map((value) => String(value || '').trim())
        .filter(Boolean);
      if (!normalizedColumnIds.length || normalizedColumnIds.length !== new Set(normalizedColumnIds).size) return false;
      const targetColumns = new Set(normalizedColumnIds);
      const columnsById = new Map();
      for (const item of affectedColumns) {
        const columnId = String(item?.column_id || '').trim();
        const orderedCardIds = Array.isArray(item?.ordered_card_ids)
          ? item.ordered_card_ids.map((value) => String(value || '').trim()).filter(Boolean)
          : null;
        if (
          !columnId
          || !targetColumns.has(columnId)
          || columnsById.has(columnId)
          || !orderedCardIds
          || orderedCardIds.length !== new Set(orderedCardIds).size
        ) return false;
        columnsById.set(columnId, orderedCardIds);
      }
      if (columnsById.size !== normalizedColumnIds.length) return false;

      const existingCards = state.snapshot.cards;
      const existingById = new Map(
        existingCards.filter((card) => card?.id).map((card) => [String(card.id), card]),
      );
      const expectedAffectedIds = new Set(
        existingCards
          .filter((card) => targetColumns.has(String(card?.column || '').trim()))
          .map((card) => String(card.id || '').trim())
          .filter(Boolean),
      );
      expectedAffectedIds.add(String(movedCard.id));
      const deltaIds = normalizedColumnIds.flatMap((columnId) => columnsById.get(columnId) || []);
      const deltaIdSet = new Set(deltaIds);
      if (
        deltaIds.length !== deltaIdSet.size
        || deltaIdSet.size !== expectedAffectedIds.size
        || Array.from(expectedAffectedIds).some((cardId) => !deltaIdSet.has(cardId))
        || deltaIds.some((cardId) => cardId !== movedCard.id && !existingById.has(cardId))
      ) return false;

      const suppressedMovedCard = applyCardSeenSuppression(movedCard);
      cacheFullCard(suppressedMovedCard);
      const movedBoardCard = boardCardFromFullCard(suppressedMovedCard);
      const reorderedCards = [];
      normalizedColumnIds.forEach((columnId) => {
        (columnsById.get(columnId) || []).forEach((cardId, position) => {
          const sourceCard = cardId === movedCard.id ? movedBoardCard : existingById.get(cardId);
          reorderedCards.push({ ...sourceCard, column: columnId, position });
        });
      });
      state.snapshot.cards = existingCards
        .filter((card) => !targetColumns.has(String(card?.column || '').trim()))
        .concat(reorderedCards);
      if (state.activeCard?.id === movedCard.id) {
        state.activeCard = state.activeCardIsFull
          ? { ...state.activeCard, ...suppressedMovedCard }
          : movedBoardCard;
      }
      if (state.mobileCard?.id === movedCard.id) {
        state.mobileCard = { ...state.mobileCard, ...suppressedMovedCard };
      }
      if (extraBoardColumnIsOpen()) {
        renderBoard();
        return true;
      }
      const cardsByColumn = buildBoardCardsByColumn(state.snapshot);
      const renderedAll = normalizedColumnIds.every(
        (columnId) => renderBoardColumnById(columnId, cardsByColumn),
      );
      if (!renderedAll) renderBoard();
      return true;
    }

    function applyBoardColumnsPatch(nextColumns) {
      if (!Array.isArray(state.snapshot?.columns) || !Array.isArray(nextColumns)) return false;
      state.snapshot.columns = sortBoardColumns(nextColumns);
      renderBoard();
      updateSnapshotStatusLine({ showSuccess: true });
      return true;
    }

    async function moveCard(cardId, columnId, beforeCardId = '') {
      const context = captureViewerRequestContext();
      const previous = state.boardMoveQueue || Promise.resolve();
      // Capture each drop now; dispatch writes in gesture order, including across columns.
      const operation = previous.catch(() => {}).then(() => perfMeasureAsync('moveCard', async () => {
        if (!context.isCurrent()) return false;
        const request = {};
        state.boardMoveRequest = request;
        state.boardMutationGeneration = (state.boardMutationGeneration || 0) + 1;
        try {
          clearCardOpenSideEffectTimer();
          const data = await api('/api/move_card', {
            method: 'POST',
            body: {
              card_id: cardId,
              column: columnId,
              before_card_id: beforeCardId || undefined,
              placement: beforeCardId ? 'start' : 'end',
              actor_name: context.actorName,
              source: 'ui',
              response_mode: 'delta',
            },
          });
          if (!context.isCurrent()) return false;
          state.boardMoveRequest = null;
          state.boardMutationGeneration += 1;
          const hasDelta = Array.isArray(data?.affected_columns);
          const patched = hasDelta
            ? applyBoardColumnOrderDelta(data?.card, data.affected_columns, data?.affected_column_ids || [])
            : applyBoardColumnCardsPatch(data?.affected_cards || [], data?.affected_column_ids || []);
          if (!patched && hasDelta) {
            await refreshSnapshot(true);
          } else if (!patched && data?.card) {
            replaceSnapshotCard(data.card);
          } else if (!patched && !data?.card) {
            await refreshSnapshot(true);
          } else {
            setStatus('ДОСКА ОБНОВЛЕНА · ' + new Date().toLocaleTimeString('ru-RU'), false);
          }
          return true;
        } catch (error) {
          if (context.isCurrent()) {
            state.boardMoveRequest = null;
            state.boardMutationGeneration += 1;
            await refreshSnapshot(true);
            if (context.isCurrent()) setStatus(error.message, true);
          }
          return false;
        } finally {
          if (state.boardMoveRequest === request) state.boardMoveRequest = null;
        }
      }));
      state.boardMoveQueue = operation;
      try {
        return await operation;
      } finally {
        if (state.boardMoveQueue === operation) state.boardMoveQueue = null;
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
