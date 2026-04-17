import logging
import json
import base64
import io
from PIL import Image
import ssl
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager

from redfish import redfish_client
from utils.logging_config import configure_logging

configure_logging()
logger = logging.getLogger("DellRedfishClient")


def make_dhe_compatible_context(
    cafile: Optional[Path] = None,
    *,
    seclevel: int = 1,
    verify: bool = True,
    tls12_only: bool = True,
) -> ssl.SSLContext:
    """
    Build an SSLContext that accepts legacy DHE handshakes (small DH groups).
    - seclevel=1 usually permits 1024-bit DH. Use 0 only as a last resort.
    - If verify=True and the server is self-signed, pass its PEM as `cafile`.
    - DHE is TLS<=1.2; set tls12_only=True to pin TLS 1.2.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.set_ciphers(f"DEFAULT:@SECLEVEL={seclevel}:DHE")
    ctx.options |= ssl.OP_NO_COMPRESSION
    if tls12_only:
        try:
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        except Exception:
            # Older Python/openssl may not support TLSVersion
            pass

    if verify:
        if cafile:
            ctx.load_verify_locations(str(cafile))
        else:
            ctx.load_default_certs()
    else:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


class SSLContextAdapter(HTTPAdapter):
    """requests adapter that injects a custom ssl_context into urllib3."""
    def __init__(self, ssl_context: ssl.SSLContext, **kwargs):
        self._ssl_context = ssl_context
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs["ssl_context"] = self._ssl_context
        self.poolmanager = PoolManager(
            num_pools=connections, maxsize=maxsize, block=block, **pool_kwargs
        )

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        proxy_kwargs["ssl_context"] = self._ssl_context
        return super().proxy_manager_for(proxy, **proxy_kwargs)

class DellRedfishClient:
    def __init__(self, base_url, username, password, verify: Optional[Union[bool, str, Path]] = None, **kwargs):
        # honor `verify` parameter (can be bool, or path to cafile).
        # Do NOT accept `verify` through `kwargs` and do NOT add a `verify` key
        # into `kwargs`. Only use the explicit `verify` parameter.
        verify_val = verify

        # Only install an https_adapter when verify is True or a cafile path is provided.
        if isinstance(verify_val, (str, Path)):
            cafile = Path(verify_val)
            ctx = make_dhe_compatible_context(cafile=cafile, verify=True)
            adapter = SSLContextAdapter(ctx)
            kwargs.setdefault("https_adapter", adapter)
        elif verify_val is True:
            # If explicit True provided, install adapter to allow DHE compatibility
            ctx = make_dhe_compatible_context(verify=True)
            adapter = SSLContextAdapter(ctx)
            kwargs.setdefault("https_adapter", adapter)
        self._standard_client = redfish_client(
            base_url=base_url,
            username=username,
            password=password,
            **kwargs,
        )
        self._standard_client.login()

    # --- REDFISH HELPER METHODS ---

    # --- DELL SPECIFIC ENDPOINTS ---

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

    def get_storage_health(self) -> List[Dict[str, Any]]:
        """Return `Status` and `PredictedMediaLifeLeftPercent` for each drive.

        Walks /redfish/v1/Systems/System.Embedded.1/Storage -> Members ->
        for each storage resource read `Drives` and query each drive resource.
        Returns a list of dicts with `@odata.id`, `Status`, and `PredictedMediaLifeLeftPercent`.
        """
        base_uri = "/redfish/v1/Systems/System.Embedded.1/Storage"
        logger.info("requesting storage collection %s", base_uri)
        resp = self.get(base_uri)

        if resp.status == 401:
            logger.warning("unauthorized access to %s (401)", base_uri)
            raise PermissionError("unauthorized")
        if resp.status != 200:
            logger.error("failed to get storage collection %s status=%s body=%s", base_uri, resp.status, getattr(resp, 'dict', None))
            raise RuntimeError(f"request failed status={resp.status}")

        try:
            data = resp.dict
        except Exception:
            logger.exception("invalid json response from %s", base_uri)
            raise ValueError("invalid json response")

        members = data.get("Members", [])
        if not isinstance(members, list) or members == []:
            logger.info("no storage members found for %s", base_uri)
            return []

        results: List[Dict[str, Any]] = []

        for m in members:
            member_id = None
            if isinstance(m, dict):
                member_id = m.get("@odata.id") or m.get("href")
            elif isinstance(m, str):
                member_id = m
            if not member_id:
                logger.debug("skipping storage member without @odata.id/href in %s", base_uri)
                continue

            logger.debug("requesting storage member %s", member_id)
            r1 = self.get(member_id)
            if r1.status != 200:
                logger.error("failed to get storage member %s status=%s", member_id, r1.status)
                continue
            try:
                d1 = r1.dict
            except Exception:
                logger.exception("invalid json for %s", member_id)
                continue

            drives = d1.get("Drives", [])
            if not isinstance(drives, list) or drives == []:
                logger.debug("no drives listed for storage member %s", member_id)
                continue

            for drv in drives:
                drive_id = None
                if isinstance(drv, dict):
                    drive_id = drv.get("@odata.id") or drv.get("href")
                elif isinstance(drv, str):
                    drive_id = drv
                if not drive_id:
                    logger.debug("skipping drive entry without @odata.id/href under %s", member_id)
                    continue

                select_uri = drive_id + ('?$select=Status,PredictedMediaLifeLeftPercent' if '?' not in drive_id else '&$select=Status,PredictedMediaLifeLeftPercent')
                logger.debug("requesting drive health %s", select_uri)
                r2 = self.get(select_uri)
                if r2.status != 200:
                    logger.error("failed to get drive %s status=%s", select_uri, r2.status)
                    results.append({"@odata.id": drive_id, "Status": None, "PredictedMediaLifeLeftPercent": None})
                    continue

                try:
                    d2 = r2.dict
                except Exception:
                    logger.exception("invalid json for %s", select_uri)
                    results.append({"@odata.id": drive_id, "Status": None, "PredictedMediaLifeLeftPercent": None})
                    continue

                status = d2.get("Status") if isinstance(d2, dict) else None
                pm = d2.get("PredictedMediaLifeLeftPercent") if isinstance(d2, dict) else None
                results.append({"@odata.id": drive_id, "Status": status, "PredictedMediaLifeLeftPercent": pm})

        logger.info("collected storage health for %d drives", len(results))
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

    def export_server_screen_shot(self, filetype: int = 2) -> bytes:
        """Export a server screenshot via the Dell LC action and return PNG bytes.

        `filetype` values: 0=LastCrashScreenShot, 1=Preview, 2=ServerScreenShot
        Returns raw PNG bytes.
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
        resp = self.post(uri, body=payload)

        if resp.status not in (200, 202):
            logger.error("ExportServerScreenShot failed %s status=%s body=%s", uri, resp.status, getattr(resp, 'dict', None))
            raise RuntimeError(f"ExportServerScreenShot failed status={resp.status}")

        data = resp.dict
        b64 = data.get("ServerScreenShotFile")
        if not b64:
            logger.error("no ServerScreenShotFile returned for %s", uri)
            raise RuntimeError("no screenshot data returned")

        try:
            raw = base64.b64decode(b64)
        except Exception:
            logger.exception("invalid base64 in ServerScreenShotFile")
            raise

        # create PIL Image from bytes
        try:
            with io.BytesIO(raw) as bio:
                im = Image.open(bio)
                im.load()
        except Exception:
            logger.exception("failed to create image from screenshot bytes")
            raise RuntimeError("invalid image data")

        # resize longest side to 1000 while preserving aspect ratio
        w, h = im.size
        max_side = max(w, h)
        if max_side > 1000:
            scale = 1000 / float(max_side)
            new_size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
            im = im.resize(new_size, Image.LANCZOS)
            logger.info("Resized screenshot from %dx%d to %dx%d", w, h, new_size[0], new_size[1])
        else:
            logger.info("Screenshot size %dx%d <=1000, not resized", w, h)

        # encode back to PNG bytes
        out = io.BytesIO()
        try:
            im.save(out, format="PNG")
            png_bytes = out.getvalue()
        except Exception:
            logger.exception("failed to encode image to PNG")
            raise RuntimeError("failed to encode image")

        logger.info("ExportServerScreenShot: returning %d bytes", len(png_bytes))
        # write PNG bytes to a temporary file and return the path
        try:
            import tempfile

            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tf:
                tf.write(png_bytes)
                temp_path = tf.name
            logger.info("ExportServerScreenShot: saved %d bytes to %s", len(png_bytes), temp_path)
            return temp_path
        except Exception:
            logger.exception("failed to write screenshot to tempfile")
            raise


    # --- PROXY METHOD ---
    def get_bios_registry(self) -> List[str]:
        """Return a list of BIOS registry attribute names from the BIOS Attribute Registry.

        Queries: /redfish/v1/Systems/System.Embedded.1/Bios/BiosRegistry
        Extracts the `AttributeName` value from each entry under
        `RegistryEntries -> Attributes` and returns them as a list of strings.
        """
        uri = "/redfish/v1/Systems/System.Embedded.1/Bios/BiosRegistry"
        logger.info("requesting BIOS registry %s", uri)
        resp = self.get(uri)

        if resp.status == 401:
            logger.warning("unauthorized access to %s (401)", uri)
            raise PermissionError("unauthorized")
        if resp.status != 200:
            logger.error("failed to get BIOS registry %s status=%s body=%s", uri, resp.status, getattr(resp, 'dict', None))
            raise RuntimeError(f"request failed status={resp.status}")

        try:
            data = resp.dict
        except Exception:
            logger.exception("invalid json response from %s", uri)
            raise ValueError("invalid json response")

        registry = data.get("RegistryEntries") or {}
        attributes = registry.get("Attributes") or []
        if not isinstance(attributes, list):
            logger.error("unexpected Attributes format in BIOS registry %s", uri)
            raise RuntimeError("invalid BIOS registry format")

        names: List[str] = []
        for a in attributes:
            if isinstance(a, dict):
                name = a.get("AttributeName")
                if isinstance(name, str):
                    names.append(name)

        logger.info("found %d BIOS registry attributes", len(names))
        return names

    def get_bios_attributes(self, attributes: Optional[Union[str, List[str]]] = None) -> Dict[str, Any]:
        """Return a mapping of requested BIOS `AttributeName` -> current value.

        `attributes` may be a comma-separated string or a list of attribute names.
        Queries: /redfish/v1/Systems/System.Embedded.1/Bios
        """
        uri = "/redfish/v1/Systems/System.Embedded.1/Bios"
        logger.info("requesting BIOS attributes %s", uri)
        resp = self.get(uri)

        if resp.status == 401:
            logger.warning("unauthorized access to %s (401)", uri)
            raise PermissionError("unauthorized")
        if resp.status != 200:
            logger.error("failed to get BIOS attributes %s status=%s body=%s", uri, resp.status, getattr(resp, 'dict', None))
            raise RuntimeError(f"request failed status={resp.status}")

        try:
            data = resp.dict
        except Exception:
            logger.exception("invalid json response from %s", uri)
            raise ValueError("invalid json response")

        attrs_map = data.get("Attributes")
        if not isinstance(attrs_map, dict):
            logger.error("unexpected Attributes format in BIOS resource %s", uri)
            raise RuntimeError("invalid BIOS attributes format")

        # if no specific attributes requested, return the full attributes map
        if attributes is None:
            return attrs_map

        # normalize requested attributes into a list
        if isinstance(attributes, str):
            wanted = [s.strip() for s in attributes.split(",") if s.strip()]
        elif isinstance(attributes, list):
            wanted = [str(s).strip() for s in attributes if str(s).strip()]
        else:
            raise ValueError("attributes must be a comma-separated string or a list of names")

        results: Dict[str, Any] = {}
        missing: List[str] = []
        for w in wanted:
            if w in attrs_map:
                results[w] = attrs_map[w]
            else:
                missing.append(w)

        if not results:
            logger.error("no matching BIOS attributes found for requested names: %s", wanted)
            raise RuntimeError("no matching BIOS attributes found")

        if missing:
            logger.warning("some requested BIOS attributes were not found: %s", missing)

        logger.info("retrieved %d BIOS attribute values", len(results))
        return results

    def get_idrac_attributes(self, group: str = "idrac", attributes: Optional[str] = None) -> Dict[str, Any]:
        """Dell-specific: Get iDRAC configuration attributes.

        Parameters
        - group: one of "idrac", "lc", or "system" (case-insensitive). Determines
          the FQDD used when querying DellAttributes.
        - attributes: optional comma-separated list of attribute names to return.

        Returns the full Attributes mapping when `attributes` is omitted, or a
        dict containing only the requested attributes when provided.
        """
        g = (group or "").lower()
        if g == "idrac":
            fqdd = "iDRAC.Embedded.1"
        elif g == "lc" or g == "lifecyclecontroller":
            fqdd = "LifecycleController.Embedded.1"
        elif g == "system":
            fqdd = "System.Embedded.1"
        else:
            logger.error("invalid value entered for group: %s", group)
            raise ValueError(f"invalid group '{group}', expected one of: idrac, lc, system")

        uri = f"/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellAttributes/{fqdd}"
        logger.info("requesting iDRAC attributes %s (group=%s attributes=%s)", uri, group, attributes)
        resp = self.get(uri)

        if resp.status == 401:
            logger.warning("unauthorized access to %s (401)", uri)
            raise PermissionError("unauthorized")
        if resp.status != 200:
            logger.error("failed to get attributes %s status=%s body=%s", uri, resp.status, getattr(resp, 'dict', None))
            raise RuntimeError(f"request failed status={resp.status}")

        try:
            data = resp.dict
        except Exception:
            logger.exception("invalid json response from %s", uri)
            raise ValueError("invalid json response")

        attributes_map = data.get("Attributes") or {}
        if attributes is None:
            return attributes_map

        # filter requested comma-separated attributes
        wanted = [s.strip() for s in str(attributes).split(",") if s.strip()]
        results: Dict[str, Any] = {}
        missing: List[str] = []
        for w in wanted:
            if w in attributes_map:
                results[w] = attributes_map[w]
            else:
                missing.append(w)

        if not results:
            logger.error("unable to locate requested attributes %s in %s", wanted, uri)
            raise RuntimeError(f"unable to locate requested attributes: {wanted}")

        if missing:
            logger.warning("some requested attributes were not found: %s", missing)

        return results

    def __getattr__(self, name):
        """
        Redirect any method calls not defined here (like .get, .patch, .post)
        to the official DMTF client.
        """
        return getattr(self._standard_client, name)