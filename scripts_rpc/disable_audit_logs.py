"""Disable OCA auditLogs"""
import logging

from godoo_rpc import OdooApiWrapper

LOGGER = logging.getLogger(__name__)


def Main(odoo_api: OdooApiWrapper):
    try:
        mod = odoo_api.session.env["auditlog.rule"]
    except Exception:
        LOGGER.warning("Disable Auditlogs: auditlog.rule model not found")
        return
    rule_ids = mod.search([("state", "=", "subscribed")])
    rules = mod.browse(rule_ids)
    if rules:
        LOGGER.info("Disabling '%s' audit log rules", len(rules))
        rules.unsubscribe()
