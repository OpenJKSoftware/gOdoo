#!/usr/bin/env bash
# Thin DevContainer adapter. Python owns lifecycle state and bootstrap decisions.

set -euo pipefail

mode=dev
dev_mode=false
skip_source_sync=false

usage() {
    cat <<'EOF'
Usage: scripts/launchodoo.sh [OPTION]

Delegate the DevContainer lifecycle to gOdoo.

  --prepare-only       Synchronize source when applicable, then run `godoo prepare`.
  --bootstrap-only     Run `godoo dev --no-launch`.
  --launch-only        Run `godoo launch` without preparation or bootstrap.
  --dev-mode           Enable Odoo development mode.
  --skip-source-sync   Do not synchronize source repositories.
  -h, --help           Show this help.
EOF
}

while (($#)); do
    case "$1" in
        --prepare-only) mode=prepare ;;
        --bootstrap-only) mode=bootstrap ;;
        --launch-only) mode=launch ;;
        --dev-mode) dev_mode=true ;;
        --skip-source-sync) skip_source_sync=true ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

sync_source=false
source_clone_archive="${SOURCE_CLONE_ARCHIVE:-false}"
if [[ "$skip_source_sync" != true && "${source_clone_archive,,}" != true ]]; then
    sync_source=true
fi

case "$mode" in
    prepare)
        if [[ "$sync_source" == true ]]; then
            godoo source get --remove-unspecified-addons
        fi
        exec godoo prepare
        ;;
    launch)
        if [[ "$dev_mode" == true ]]; then
            exec godoo launch --dev-mode
        fi
        exec godoo launch
        ;;
    bootstrap|dev)
        # X-Sendfile is a DevContainer profile concern; Odoo bootstrap itself
        # remains owned by the Python lifecycle service.
        export ODOO_BIN_BOOTSTRAP_ARGS="${ODOO_BIN_BOOTSTRAP_ARGS:+${ODOO_BIN_BOOTSTRAP_ARGS} }--x-sendfile"
        command=(godoo dev)
        if [[ "$sync_source" == true ]]; then
            command+=(--sync-sources)
        fi
        if [[ "$dev_mode" == true ]]; then
            command+=(--dev-mode)
        fi
        if [[ "$mode" == bootstrap ]]; then
            command+=(--no-launch)
        fi
        exec "${command[@]}"
        ;;
esac
