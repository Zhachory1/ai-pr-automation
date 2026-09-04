#!/bin/sh
set -eu

root="${CODE_ROOT%/}"
vault="${SWARMVAULT_VAULT%/}"
case "$root:$vault" in
  /*:/*) ;;
  *) echo "CODE_ROOT and SWARMVAULT_VAULT must be absolute paths" >&2; exit 2 ;;
esac
case "/$root/$vault/" in
  *'/./'*|*'/../'*) echo "CODE_ROOT and SWARMVAULT_VAULT must not contain . or .." >&2; exit 2 ;;
esac
case "$vault" in
  "$root"|"$root"/*) echo "SWARMVAULT_VAULT must be outside CODE_ROOT" >&2; exit 2 ;;
esac
