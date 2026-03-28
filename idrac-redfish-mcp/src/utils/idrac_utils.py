"""Helper utilities for iDRAC built on top of iDRACRedfish.

Provides an `iDRACUtils` class with convenience helpers such as
`get_idrac_generation` which reads Dell attributes and extracts the
`Info.1.HWModel` value to determine generation information.
"""
from typing import Optional
import re
import logging
import requests

from .idrac_redfish import iDRACRedfish


class iDRACUtils:
    def __init__(self, client: iDRACRedfish) -> None:
        """Wrap an existing `iDRACRedfish` client instance.

        The caller is responsible for creating and authenticating the
        `iDRACRedfish` instance (login/logout) if needed.
        """
        self.client = client

    def get_idrac_generation(self) -> Optional[str]:
        """Fetch the Dell attributes and return a generation identifier.

        Calls `/Managers/iDRAC.Embedded.1/Oem/Dell/DellAttributes/iDRAC.Embedded.1`
        and reads `Attributes["Info.1.HWModel"]`. Returns the first numeric
        group if present, otherwise returns the raw attribute string. If the
        attribute is missing, returns `None`.
        """
        resp = self.client.get(
            '/Managers/iDRAC.Embedded.1/Oem/Dell/DellAttributes/iDRAC.Embedded.1'
        )
        data = resp.json()
        attrs = data.get('Attributes') if isinstance(data, dict) else None
        if not isinstance(attrs, dict):
            return None

        hw = attrs.get('Info.1.HWModel')
        if not hw:
            return None

        m = re.search(r"(\d+)", str(hw))
        if m:
            return m.group(1)
        return str(hw)

    def get_server_generation(self) -> Optional[str]:
        """Fetch the server `SystemGeneration` value from the Systems resource.

        Returns the raw `SystemGeneration` value or `None` on failure. Logs
        errors for 401 and other non-200 responses rather than exiting.
        """
        path = '/Systems/System.Embedded.1'
        try:
            resp = self.client.get(path)
            data = resp.json()
        except requests.HTTPError as e:
            resp = getattr(e, 'response', None)
            data = None
            if resp is not None:
                try:
                    data = resp.json()
                except Exception:
                    data = None

                if resp.status_code == 401:
                    logging.error(
                        "Status code 401 detected, check iDRAC credentials or session token"
                    )
                    return None
                logging.warning("Unable to get server generation, error: %s", data)
                return None
            raise

        if not isinstance(data, dict):
            logging.error("Unable to get server generation, invalid JSON response")
            return None

        try:
            return data["Oem"]["Dell"]["DellSystem"]["SystemGeneration"]
        except Exception:
            logging.error("Unable to get server generation from JSON output")
            return None
