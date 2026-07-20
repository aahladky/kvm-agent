#!/usr/bin/env bash
# install_hooks.sh -- one-time opt-in: installs a real git pre-commit hook that runs
# tools/check_layout.py before every commit made from a plain terminal (a committed
# .claude/settings.json PreToolUse hook already covers commits made through Claude
# Code sessions on this repo automatically -- this is the belt for everything else,
# since .git/hooks/ is never itself tracked/committed by git). See
# docs/PROJECT_LAYOUT.md.
#
#   bash tools/install_hooks.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK_PATH="$REPO_ROOT/.git/hooks/pre-commit"

cat > "$HOOK_PATH" <<'EOF'
#!/usr/bin/env bash
# Installed by tools/install_hooks.sh -- do not edit by hand, re-run that script instead.
set -e
REPO_ROOT="$(git rev-parse --show-toplevel)"
python3 "$REPO_ROOT/tools/check_layout.py"
EOF

chmod +x "$HOOK_PATH"
echo "installed pre-commit hook -> $HOOK_PATH"
