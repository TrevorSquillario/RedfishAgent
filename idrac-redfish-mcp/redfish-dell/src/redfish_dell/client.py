import logging
import json
import base64
from datetime import datetime
from typing import Any, Dict, List, Optional

from redfish import redfish_client
from .logging_config import configure_logging

configure_logging()
logger = logging.getLogger("DellRedfishClient")

class DellRedfishClient:
    def __init__(self, base_url, username, password, **kwargs):
        # 1. Initialize the official DMTF client
        self._standard_client = redfish_client(
            base_url=base_url, 
            username=username, 
            password=password, 
            **kwargs
        )
        self._standard_client.login()

    # --- REDFISH HELPER METHODS ---

    # --- DELL SPECIFIC ENDPOINTS ---
    
    def get_idrac_attributes(self):
        """Dell-specific: Get iDRAC configuration attributes."""
        return self._standard_self._standard_client.get("/redfish/v1/Managers/iDRAC.Embedded.1/Attributes")

    def get_lifecycle_logs(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        severity: Optional[str] = None,
        top: Optional[int] = 10,
        skip: Optional[int] = None,
    ) -> List[Dict[str, Any]]:

        # default LC log entries resource
        uri = "/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Lclog/Entries"

        # build filters: support date range and optional severity filter
        filters: List[str] = []
        if start_date and end_date:
            filters.append(f"Created ge '{start_date}' and Created le '{end_date}'")

        # map predefined severity options to the server's Severity values
        if severity:
            sev = severity.lower()
            allowed = {
                "informational": "OK",
                "critical": "Critical",
                "warning": "Warning",
            }
            if sev not in allowed:
                raise ValueError(f"invalid severity '{severity}', valid options: {list(allowed.keys())}")
            filters.append(f"Severity eq '{allowed[sev]}'")

        # build query string parts (filter, $top, $skip)
        parts: List[str] = []
        if filters:
            parts.append(f"$filter={' and '.join(filters)}")

        # validate and append top/skip if provided
        if top is not None:
            try:
                top_i = int(top)
                if top_i < 0:
                    raise ValueError("$top must be non-negative")
            except Exception:
                raise ValueError("invalid $top value")
            parts.append(f"$top={top_i}")

        if skip is not None:
            try:
                skip_i = int(skip)
                if skip_i < 0:
                    raise ValueError("$skip must be non-negative")
            except Exception:
                raise ValueError("invalid $skip value")
            parts.append(f"$skip={skip_i}")

        query_uri = uri + ('?' + '&'.join(parts) if parts else '')

        collected: List[Dict[str, Any]] = []

        # initial request
        try:
            logger.info(f"Executing Redfish URI: {query_uri}")
            resp = self._standard_client.get(query_uri)
        except Exception:
            logger.exception("request failed for %s", query_uri)
            raise

        try:
            data = resp.dict
        except Exception:
            logger.exception("invalid json response from %s", query_uri)
            raise ValueError("invalid json response")

        if resp.status == 401:
            logger.warning("unauthorized access to %s (401)", query_uri)
            raise PermissionError("unauthorized")
        if resp.status != 200:
            logger.error("request failed %s status=%s body=%s", query_uri, resp.status, data)
            raise RuntimeError(f"request failed status={resp.status}")

        if "Members" not in data:
            logger.error("no 'Members' key in response from %s", query_uri)
            raise RuntimeError("no 'Members' key in response")

        if data.get("Members") == []:
            logger.info("no LC logs in date range or resource for %s", query_uri)
            return []

        collected.extend(data.get("Members", []))

        # paginate
        next_link = data.get("Members@odata.nextLink")
        while next_link:
            try:
                resp = self._standard_client.get(next_link)
            except Exception:
                logger.exception("failed following nextLink: %s", next_link)
                break
            if resp.status != 200:
                logger.error("nextLink returned status %s for %s", resp.status, next_link)
                break
            try:
                data = resp.dict
            except Exception:
                logger.exception("invalid json on nextLink %s", next_link)
                break
            if "Members" not in data or data.get("Members") == []:
                break
            collected.extend(data.get("Members", []))
            next_link = data.get("Members@odata.nextLink")

        logger.info("collected %d LC log entries for %s", len(collected), next_link)
        return collected

    # --- PROXY METHOD ---
    
    def __getattr__(self, name):
        """
        Redirect any method calls not defined here (like .get, .patch, .post)
        to the official DMTF client.
        """
        return getattr(self._standard_client, name)

    # --- DELL ROLLUP / FAULT / HEALTH HELPERS ---

    def get_device_rollup_health_status(self, device_filter: str = "all") -> List[Dict[str, Any]]:
        """Return DellRollupStatus Members filtered by `device_filter`.

        `device_filter` may be "all" or a comma-separated list of substrings
        that are matched (case-insensitive) against each Member's `SubSystem`.
        """
        uri = "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellRollupStatus"
        logger.info("requesting Dell rollup status %s", uri)
        resp = self.get(uri)

        if resp.status == 401:
            logger.warning("unauthorized access to %s (401)", uri)
            raise PermissionError("unauthorized")
        if resp.status != 200:
            logger.error("failed to get rollup status %s status=%s body=%s", uri, resp.status, getattr(resp, 'dict', None))
            raise RuntimeError(f"request failed status={resp.status}")

        data = resp.dict
        members = data.get("Members", [])

        if device_filter.lower() == "all":
            if members == []:
                logger.info("no rollup status members found for %s", uri)
            return members

        wanted = [p.strip().lower() for p in device_filter.split(",") if p.strip()]
        matched: List[Dict[str, Any]] = []
        for m in members:
            sub = (m.get("SubSystem") or "").lower()
            for w in wanted:
                if w in sub:
                    matched.append(m)
                    break

        if not matched:
            logger.warning("no supported device(s) detected for filter '%s'", device_filter)
        else:
            logger.info("found %d rollup entries for filter '%s'", len(matched), device_filter)

        return matched

    def get_device_fault_details(self) -> List[Dict[str, Any]]:
        """Return FaultList Entries from the iDRAC FaultList log service."""
        uri = "/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/FaultList/Entries"
        logger.info("requesting fault list entries %s", uri)
        resp = self.get(uri)

        if resp.status == 401:
            logger.warning("unauthorized access to %s (401)", uri)
            raise PermissionError("unauthorized")
        if resp.status != 200:
            logger.error("failed to get fault list %s status=%s body=%s", uri, resp.status, getattr(resp, 'dict', None))
            raise RuntimeError(f"request failed status={resp.status}")

        data = resp.dict
        members = data.get("Members", [])
        if members == []:
            logger.info("no fault events detected in %s", uri)
        return members

    def get_error_and_event_registry(self, message_id: str) -> Any:
        """Query the iDRAC Message Registry (EEMIRegistry) which contains detailed descriptions for error and event message IDs with suggestions for resolutions to errors.

        If `message_id` is provided, return the specific message entry
        (e.g. "ACC0001"). Otherwise return the entire `Messages` mapping.
        """
        uri = "/redfish/v1/Registries/Messages/EEMIRegistry"
        logger.info("requesting message registry %s", uri)
        resp = self.get(uri)

        if resp.status == 401:
            logger.warning("unauthorized access to %s (401)", uri)
            raise PermissionError("unauthorized")
        if resp.status != 200:
            logger.error("failed to get message registry %s status=%s body=%s", uri, resp.status, getattr(resp, 'dict', None))
            raise RuntimeError(f"request failed status={resp.status}")

        try:
            data = resp.dict
        except Exception:
            logger.exception("invalid json response from %s", uri)
            raise ValueError("invalid json response")

        messages = data.get("Messages") or {}
        if message_id:
            return messages.get(message_id)
        return messages

    def get_memory_processor_health_information(self, device_name: str) -> Dict[str, Optional[str]]:
        """For a given `device_name` collection, query each member's Status/Health.

        Returns a mapping of member short-name -> Health value (or None if absent).
        """
        base_uri = f"/redfish/v1/Systems/System.Embedded.1/{device_name}"
        logger.info("requesting members for %s", base_uri)
        resp = self.get(base_uri)

        if resp.status == 401:
            logger.warning("unauthorized access to %s (401)", base_uri)
            raise PermissionError("unauthorized")
        if resp.status != 200:
            logger.error("failed to get resource %s status=%s body=%s", base_uri, resp.status, getattr(resp, 'dict', None))
            raise RuntimeError(f"request failed status={resp.status}")

        data = resp.dict
        members = data.get("Members", [])
        if not isinstance(members, list) or members == []:
            logger.info("no members found for %s", base_uri)
            return {}

        results: Dict[str, Optional[str]] = {}
        for m in members:
            member_id = None
            if isinstance(m, dict):
                member_id = m.get("@odata.id") or m.get("href")
            elif isinstance(m, str):
                member_id = m
            if not member_id:
                logger.debug("skipping member without @odata.id/href in %s", base_uri)
                continue

            # ensure path-only URI for client.get; if member_id includes host, use as-is
            select_uri = member_id + ('?$select=Status/Health' if '?' not in member_id else '&$select=Status/Health')
            logger.debug("requesting Health for %s", select_uri)
            r2 = self.get(select_uri)
            if r2.status != 200:
                logger.error("failed to get health for %s status=%s", select_uri, r2.status)
                results[member_id.split("/")[-1]] = None
                continue
            try:
                d2 = r2.dict
            except Exception:
                logger.exception("invalid json for %s", select_uri)
                results[member_id.split("/")[-1]] = None
                continue

            health = None
            if isinstance(d2, dict):
                health = (d2.get("Status") or {}).get("Health")
            results[member_id.split("/")[-1]] = health

        logger.info("collected health for %d members under %s", len(results), base_uri)
        return results

    def get_server_slot_info(self, slot_type: Optional[str] = None) -> Dict[str, Any]:
        """Return Dell Slots collection as JSON (no file output).

        Returns a dict with a `timestamp` and combined `members` list.

        Parameters
        - slot_type: Optional[str]
            Filter returned slot entries by the Dell `ConnectorLayout` value.
            Accepts a single value or a comma-separated list (case-insensitive).
            Use the string "all" or omit the parameter to return all entries.

        Supported `ConnectorLayout` examples observed in Dell iDRAC:
        - Fan
        - IDSDM
        - Processor
        - PowerSupply
        - DIMM
        - SDCard
        - PCI-E
        - PhysicalDisk

        Examples
        - `get_server_slot_info()` -> return all members
        - `get_server_slot_info("DIMM")` -> return only DIMM slots
        - `get_server_slot_info("Fan,Processor")` -> return Fan or Processor slots
        """
        uri = "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellSlots"
        logger.info("requesting server slot information %s", uri)
        resp = self.get(uri)

        if resp.status == 401:
            logger.warning("unauthorized access to %s (401)", uri)
            raise PermissionError("unauthorized")
        if resp.status != 200:
            logger.error("GET failed %s status=%s body=%s", uri, resp.status, getattr(resp, 'dict', None))
            raise RuntimeError(f"request failed status={resp.status}")

        data = resp.dict
        if "Members" not in data:
            logger.error("no 'Members' key in response from %s", uri)
            raise RuntimeError("no 'Members' key in response")

        members: List[Dict[str, Any]] = []
        if isinstance(data.get("Members"), list):
            members.extend(data.get("Members", []))

        # paginate using $skip increments of 50 until empty or server reports out-of-range
        skip = 50
        while True:
            next_uri = uri + f"?$skip={skip}"
            logger.debug("requesting paged slots %s", next_uri)
            r = self.get(next_uri)
            if r.status != 200:
                # attempt to inspect error message to decide if we've reached the end
                try:
                    err = r.dict
                    info_list = err.get("error", {}).get("@Message.ExtendedInfo", [])
                    if info_list and isinstance(info_list, list):
                        msg = info_list[0].get("Message", "")
                        if "query parameter $skip is out of range" in msg:
                            break
                except Exception:
                    pass
                logger.error("paged GET failed %s status=%s body=%s", next_uri, r.status, getattr(r, 'dict', None))
                raise RuntimeError(f"paged request failed status={r.status}")

            try:
                page = r.dict
            except Exception:
                logger.exception("invalid json for %s", next_uri)
                break

            if not page.get("Members"):
                break

            members.extend(page.get("Members", []))
            skip += 50

        # apply optional filtering by ConnectorLayout / slot type
        if slot_type and slot_type.lower() != "all":
            wanted = [s.strip().lower() for s in str(slot_type).split(",") if s.strip()]
            filtered: List[Dict[str, Any]] = []
            for m in members:
                layout = (m.get("ConnectorLayout") or "").lower() if isinstance(m, dict) else ""
                if any(w == layout for w in wanted):
                    filtered.append(m)
            logger.info("filtered server slot members by slot_type=%s -> %d members", slot_type, len(filtered))
            members = filtered

        logger.info("collected %d server slot members from %s", len(members), uri)
        return members

    def export_server_screen_shot(self, filetype: int = 2, output_path: str = "export_screenshot.png") -> str:
        """Export a server screenshot via the Dell LC action and write a PNG.

        `filetype` values: 0=LastCrashScreenShot, 1=Preview, 2=ServerScreenShot
        Returns the path to the written PNG file.
        """
        uri = "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService/Actions/DellLCService.ExportServerScreenShot"

        mapping = {
            0: "LastCrashScreenShot",
            1: "Preview",
            2: "ServerScreenShot",
        }

        # accept numeric strings as well
        try:
            ft_i = int(filetype)
        except Exception:
            raise ValueError("filetype must be 0, 1, or 2")

        if ft_i not in mapping:
            raise ValueError("filetype must be 0, 1, or 2")

        payload = {"FileType": mapping[ft_i]}
        logger.info("posting ExportServerScreenShot action %s payload=%s", uri, payload)
        resp = self.post(uri, json=payload)

        if resp.status not in (200, 202):
            logger.error("ExportServerScreenShot failed %s status=%s body=%s", uri, resp.status, getattr(resp, 'dict', None))
            raise RuntimeError(f"ExportServerScreenShot failed status={resp.status}")

        data = resp.dict
        b64 = data.get("ServerScreenShotFile")
        if not b64:
            logger.error("no ServerScreenShotFile returned for %s", uri)
            raise RuntimeError("no screenshot data returned")

        try:
            img = base64.b64decode(b64)
        except Exception:
            logger.exception("invalid base64 in ServerScreenShotFile")
            raise

        try:
            with open(output_path, "wb") as fh:
                fh.write(img)
        except Exception:
            logger.exception("failed to write screenshot to %s", output_path)
            raise

        logger.info("screenshot written to %s", output_path)
        return output_path