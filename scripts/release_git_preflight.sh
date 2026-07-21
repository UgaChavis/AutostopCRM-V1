#!/usr/bin/env bash

# Shared fail-closed Git checks for the production deploy entrypoint.
# The functions print only a verified commit SHA on success. Git diagnostics,
# remote URLs, and dirty filenames are deliberately not forwarded.

release_git_error() {
  printf 'ERROR: %s\n' "$1" >&2
  return 2
}

release_git_assert_exact_state() {
  local label="$1"
  local checkout="$2"
  local expected_branch="$3"
  local expected_head="${4:-}"
  local checkout_root repo_root branch head status

  if [[ ! -d "$checkout" ]]; then
    release_git_error "$label checkout is unavailable."
    return
  fi
  if ! checkout_root="$(readlink -f -- "$checkout" 2>/dev/null)" \
    || [[ -z "$checkout_root" ]]; then
    release_git_error "$label checkout path cannot be resolved."
    return
  fi
  if ! repo_root="$(git -C "$checkout" rev-parse --show-toplevel 2>/dev/null)" \
    || ! repo_root="$(readlink -f -- "$repo_root" 2>/dev/null)" \
    || [[ "$repo_root" != "$checkout_root" ]]; then
    release_git_error "$label source must be the root of a Git checkout."
    return
  fi
  if ! branch="$(git -C "$checkout" symbolic-ref --quiet --short HEAD 2>/dev/null)"; then
    release_git_error "$label checkout must not use a detached HEAD."
    return
  fi
  if [[ "$branch" != "$expected_branch" ]]; then
    release_git_error "$label checkout must be on branch $expected_branch."
    return
  fi
  if ! head="$(git -C "$checkout" rev-parse --verify 'HEAD^{commit}' 2>/dev/null)"; then
    release_git_error "$label HEAD cannot be resolved to a commit."
    return
  fi
  if [[ -n "$expected_head" && "$head" != "$expected_head" ]]; then
    release_git_error "$label HEAD changed after release preflight."
    return
  fi
  if ! status="$(git -C "$checkout" status --porcelain=v1 --untracked-files=all 2>/dev/null)"; then
    release_git_error "$label checkout status cannot be verified."
    return
  fi
  if [[ -n "$status" ]]; then
    release_git_error "$label checkout is not clean, including untracked files."
    return
  fi

  printf '%s\n' "$head"
}

release_git_verify_fetched_checkout() {
  local label="$1"
  local checkout="$2"
  local expected_branch="$3"
  local remote="$4"
  local remote_branch="$5"
  local before_head after_head remote_head candidate remote_found=0

  if [[ -z "$remote" || "$remote" == -* || "$remote" == *[[:space:]]* ]]; then
    release_git_error "$label Git remote name is invalid."
    return
  fi
  if ! git check-ref-format "refs/heads/$remote_branch" >/dev/null 2>&1; then
    release_git_error "$label remote branch name is invalid."
    return
  fi
  if ! before_head="$(
    release_git_assert_exact_state "$label" "$checkout" "$expected_branch"
  )"; then
    return 2
  fi
  while IFS= read -r candidate; do
    if [[ "$candidate" == "$remote" ]]; then
      remote_found=1
      break
    fi
  done < <(git -C "$checkout" remote 2>/dev/null)
  if (( remote_found != 1 )); then
    release_git_error "$label configured Git remote is unavailable."
    return
  fi
  if ! git -C "$checkout" fetch --quiet --no-tags \
    "$remote" "refs/heads/$remote_branch" >/dev/null 2>&1; then
    release_git_error "$label exact remote branch fetch failed."
    return
  fi
  if ! remote_head="$(
    git -C "$checkout" rev-parse --verify 'FETCH_HEAD^{commit}' 2>/dev/null
  )"; then
    release_git_error "$label fetched commit cannot be resolved."
    return
  fi
  if ! after_head="$(
    release_git_assert_exact_state "$label" "$checkout" "$expected_branch" "$before_head"
  )"; then
    return 2
  fi
  if [[ "$after_head" != "$remote_head" ]]; then
    release_git_error "$label HEAD does not match the fetched remote branch."
    return
  fi

  printf '%s\n' "$after_head"
}
