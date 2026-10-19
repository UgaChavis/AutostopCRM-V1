    // Freeze this page's generation. Never discover or refresh a newer address
    // for an already queued operation, and leave explicit stale URLs unchanged.
    const COLD_BOARD_PREFIX = /^\/__cold\/[0-9a-f]{64}(?=\/|$)/.exec(window.location?.pathname || '')?.[0] || '';
    function autostopBoardUrl(value) {
      if (!COLD_BOARD_PREFIX || typeof value !== 'string' || !value.startsWith('/')
          || value.startsWith('//') || value.startsWith('/__cold/')) return value;
      return COLD_BOARD_PREFIX + value;
    }
