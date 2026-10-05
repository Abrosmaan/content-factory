#!/usr/bin/env bash
# Установить git-hooks (post-commit auto-push) для текущего clone'а.
# Идемпотентно: можно гонять повторно.
#
#   make hooks-install   # или: bash scripts/install-hooks.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .git ]]; then
  echo "Not a git repo (no .git)" >&2
  exit 1
fi

hooks_dir="$(git rev-parse --git-path hooks)"
mkdir -p "$hooks_dir"

src="$ROOT/scripts/hooks/post-commit"
dst="$hooks_dir/post-commit"

cp "$src" "$dst"
chmod +x "$dst"
echo "installed: $dst"
echo "(disable a single commit: CF_NO_AUTOPUSH=1 git commit ...)"
