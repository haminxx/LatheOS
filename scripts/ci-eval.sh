#!/usr/bin/env bash
# Evaluate a single LatheOS nixosConfiguration attribute path and, on failure,
# spill the last 200 lines of the stderr into $GITHUB_STEP_SUMMARY so the
# public run page shows the actual error without needing repo write access.
#
# Usage:
#   scripts/ci-eval.sh <config-name> <attr-path>
#
#   scripts/ci-eval.sh latheos-x86_64 config.system.build.toplevel.drvPath

set -u -o pipefail

CONFIG="${1:?config name required}"
ATTR="${2:?attr path required}"

LOG="$(mktemp)"
if nix eval \
     --no-write-lock-file \
     --show-trace \
     ".#nixosConfigurations.${CONFIG}.${ATTR}" \
     >"$LOG" 2>&1
then
    cat "$LOG"
    exit 0
fi

RC=$?
cat "$LOG"

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    {
        echo "### eval \`${CONFIG}\` FAILED (rc=${RC})"
        echo ""
        echo "Attribute: \`${ATTR}\`"
        echo ""
        echo "\`\`\`"
        tail -n 200 "$LOG"
        echo "\`\`\`"
    } >> "$GITHUB_STEP_SUMMARY"
fi
exit "$RC"
