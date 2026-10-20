#!/usr/bin/env bash
set -eu
printf '%s\n' \
  'The guarded automatic release controller has been retired.' \
  'Use docs/OPERATIONS_RUNBOOK.md#deploy for an explicitly authorized offline maintenance window.' >&2
exit 2
