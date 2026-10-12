    function applyCardClientLinkResult(card) {
      const previous = state.activeCard || {};
      const draft = currentCardPayload();
      const oldProfile = cloneVehicleProfile(previous.vehicle_profile);
      const draftProfile = cloneVehicleProfile(draft.vehicle_profile);
      const editedProfile = Object.keys(draftProfile).filter((key) => JSON.stringify(draftProfile[key]) !== JSON.stringify(oldProfile[key]));
      const edited = ['title', 'vehicle', 'description'].filter((key) => String(draft[key] || '') !== String(previous[key] || ''));
      const editedTags = JSON.stringify(normalizeDraftTags(draft.tags)) !== JSON.stringify(normalizeDraftTags(previous.tag_items || previous.tags));
      applyCardModalState(card);
      for (const key of edited) {
        if (key === 'description') setCardDescriptionValue(draft.description);
        else if (key === 'title') els.cardTitle.value = draft.title;
        else els.cardVehicle.value = draft.vehicle;
      }
      if (editedProfile.length) {
        const profile = cloneVehicleProfile(card.vehicle_profile);
        editedProfile.forEach((key) => { profile[key] = draftProfile[key]; });
        applyVehicleProfileToForm(profile);
      }
      if (editedTags) { state.draftTags = normalizeDraftTags(draft.tags); renderColorTags(); }
      state.boardMutationGeneration += 1;
      applySavedCardLocalPatch(card);
      state.activeCard = card;
      syncCardSaveDirtyState();
    }
