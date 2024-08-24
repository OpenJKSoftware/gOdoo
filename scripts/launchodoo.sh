#!/bin/bash
# Odoo Launch Script. Runs Migrations, if Specified in env

echo "=> gOdoo Container Launch Script"

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

MIGRATIONS_DIR="$DIR/migrations"

if [ ! -n $SOURCE_CLONE_ARCHIVE ];then
    # Only ensure Source, when we are not in source clone mode
    godoo source get --remove-unspecified-addons
fi

apply_shell_migrations() {
    # Function to Iterate Python scripts in directory and Pass to odoo-bin shell
    local MIG_DIR=$1

    echo "=> Applying Migrations in $MIG_DIR"
    for file in "$MIG_DIR"/*.py; do
        [ -e "$file" ] || continue
        echo "=> Applying migration $file"
        godoo -v shell "$(cat "$file")"
    done
}

godoo db odoo-bootstrapped > /dev/null
GODOO_DB_STATE=$?
set -e
if [ $GODOO_DB_STATE -eq 21 ] || [ $GODOO_DB_STATE -eq 20 ]; then
    echo "=> Odoo DB Does Not Exist"
    ODOO_BIN_BOOTSTRAP_ARGS+=" --x-sendfile"

    godoo bootstrap --extra-cmd-args="$ODOO_BIN_BOOTSTRAP_ARGS"

    # Because of X-sendfile, we need to set the report.url to go through Nginx
    godoo db query "UPDATE ir_config_parameter SET value='http://127.0.0.1:80' WHERE key = 'report.url'"

    if [ -n "$GODOO_LAUNCH_STAGE" ]; then
        if [[ -n $GODOO_DEV_SET_PW ]]; then
            echo "=> Setting Passwords"
            godoo db set-passwords admin
            godoo db query "UPDATE res_users SET login='admin' WHERE id=2"
        fi
        apply_shell_migrations "$MIGRATIONS_DIR/staging"
    fi
    godoo db odoo-bootstrapped > /dev/null
    GODOO_DB_STATE=$?
fi

if [ $GODOO_DB_STATE -eq 0 ]; then
    echo "=> Running gOdoo"
    godoo launch --no-install-workspace-modules $GODOO_LAUNCH_ARGS
else
    echo "=> Odoo DB State Return Code: $GODOO_DB_STATE"
    echo "=> gOdoo Failed to Bootstrap"
    exit 1
fi
