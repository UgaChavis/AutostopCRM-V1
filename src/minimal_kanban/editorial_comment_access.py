"""Fail-closed switch for the separately authorized order-comment capability."""

import os

ENV_NAME = "AUTOSTOP_CRM_REPAIR_ORDER_COMMENT_EDITORIAL_ENABLED"


def editorial_comment_enabled() -> bool:
    return os.environ.get(ENV_NAME, "") == "1"
