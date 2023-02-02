import logging
import os

LOGGER = logging.getLogger(__name__)


def check_dangerous_command():
    isdev = str(os.getenv("WORKSPACE_IS_DEV"))
    if not isdev.lower() == "true":
        LOGGER.warning(
            """This function is dangerous in Production environments.
Please set 'WORKSPACE_IS_DEV=true' as environment Variable to continue
            """
        )
        exit(1)
