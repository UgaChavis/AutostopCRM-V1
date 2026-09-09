from __future__ import annotations

import urllib.request


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def urlopen_no_redirect(request: urllib.request.Request | str, *, timeout: float):
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)
