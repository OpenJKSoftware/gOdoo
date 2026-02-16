"""Render an Odoo report to HTML and print to stdout.

This script renders a QWeb report for given record IDs and outputs the HTML.
It is executed in the Odoo shell environment via:

    godoo shell-script render_report <report_name> <record_id> [<record_id> ...]

Example:
    godoo shell-script render_report sale.report_saleorder 1
    godoo shell-script render_report sale.report_saleorder 1 2 3

Flags:
    --keep-base64  Keep base64 content instead of replacing with placeholder

"""

import logging
import re
import sys
from xml.dom import minidom

from odoo import api

LOGGER = logging.getLogger(__name__)

env: api.Environment = env  # Just to silence pyright # pyright: ignore # NOQA

# script_args is injected by godoo shell-script command
script_args: list[str] = script_args  #  pyright: ignore # NOQA


def replace_base64_with_placeholder(html_string: str) -> str:
    """Replace base64 content with a placeholder.

    Replaces long base64 strings (data URIs, embedded images, etc.) with
    a placeholder to make output more readable.

    Parameters
    ----------
    html_string : str
        HTML string potentially containing base64 content

    Returns:
    -------
    str
        HTML string with base64 content replaced by placeholder
    """
    # Match data URIs with base64 content and replace with placeholder
    # Matches patterns like: data:image/png;base64,... or similar
    pattern = r"data:[^/]+/[^;]+;base64,[A-Za-z0-9+/]+={0,2}"
    replacement = "data:[MIME];base64,[BASE64_CONTENT_PLACEHOLDER]"
    return re.sub(pattern, replacement, html_string)


def render_report(report_name: str, record_ids: list[int], replace_base64: bool = True):
    """Render an Odoo report to HTML.

    Parameters
    ----------
    report_name : str
        Name of the report to render (e.g., 'sale.report_saleorder')
    record_ids : list[int]
        List of record IDs to render
    replace_base64 : bool, optional
        Replace base64 content with placeholder (True by default)
    """
    try:
        report_model = env["ir.actions.report"]
        html_content, _ = report_model._render_qweb_html(report_name, record_ids)

        if html_content:
            # Decode bytes to string
            html_string = html_content.decode("utf-8") if isinstance(html_content, bytes) else html_content

            # Replace base64 content with placeholder if enabled
            if replace_base64:
                html_string = replace_base64_with_placeholder(html_string)

            # Try to pretty print the HTML
            try:
                dom = minidom.parseString(html_string)
                pretty_html = dom.toprettyxml(indent="  ")
                # Remove extra blank lines that minidom adds
                print("\n".join([line for line in pretty_html.split("\n") if line.strip()]))
            except Exception:
                # If pretty printing fails, just print the raw HTML
                print(html_string)
        else:
            LOGGER.error("Failed to render report: %s", report_name)
            sys.exit(1)

    except ValueError:
        LOGGER.exception("Report not found")
        sys.exit(1)
    except Exception:
        LOGGER.exception("Error rendering report %s", report_name)
        sys.exit(1)


# Main execution: Parse script_args and call render_report
if not script_args:
    LOGGER.error("Usage: godoo shell-script render_report <report_name> <record_id> [<record_id> ...] [--keep-base64]")
    sys.exit(1)

if len(script_args) < 2:
    LOGGER.error("Missing arguments. Need at least report_name and one record_id")
    sys.exit(1)

# Check for --keep-base64 flag
keep_base64_flag = "--keep-base64" in script_args
filtered_args = [arg for arg in script_args if arg != "--keep-base64"]

# Now extract report_name and record_ids from filtered args
if len(filtered_args) < 2:
    LOGGER.error("Missing arguments. Need at least report_name and one record_id")
    sys.exit(1)

report_name = filtered_args[0]
record_ids = [int(rid) for rid in filtered_args[1:]]

# render_report with replace_base64 being False if --keep-base64 was passed
render_report(report_name, record_ids, replace_base64=not keep_base64_flag)
