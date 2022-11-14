#!/bin/bash

PROJ_FOLDER=$(dirname $(readlink -f $0))
PROJ_FOLDER=$(dirname $PROJ_FOLDER)
source $PROJ_FOLDER/.env

DUMP_FOLDER="$PROJ_FOLDER/sql"

if [ -z $ODOO_DB_HOST ];
then
    echo running on socket
    PGPASSWORD=$ODOO_DB_PASSWORD pg_dumpall -U $ODOO_DB_USER > $DUMP_FOLDER/odoo.sql
else
    echo Running On host
    PGPASSWORD=$ODOO_DB_PASSWORD pg_dumpall -h $ODOO_DB_HOST -U $ODOO_DB_USER > $DUMP_FOLDER/odoo.sql
fi
