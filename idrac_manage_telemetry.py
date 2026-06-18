# EnableOrDisableAllTelemetryReports.py Python script using Redfish API to Enable or Disable All Telemetry Reports
# with Default/Existing settings.
#
#
# _author_ = Sankunny Jayaprasad <Sankunny.Jayaprasad@Dell.com>
# _author_ = Texas Roemer <Texas_Roemer@Dell.com>
# _version_ = 2.0
#
# Copyright (c) 2022, Dell, Inc.
#
# This software is licensed to you under the GNU General Public License,
# version 2 (GPLv2). There is NO WARRANTY for this software, express or
# implied, including the implied warranties of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. You should have received a copy of GPLv2
# along with this software; if not, see
# http://www.gnu.org/licenses/old-licenses/gpl-2.0.txt.
#

import argparse
import csv
import json
import logging
import sys
import warnings
import requests
import yaml
import os
import threading
import urllib.parse

warnings.filterwarnings("ignore")
#logging.getLogger().setLevel(logging.INFO)  # Change to logging.DEBUG for detailed logs
logging.basicConfig(format='%(message)s', stream=sys.stdout, level=logging.INFO)

# Listener destination for Redfish event subscriptions (override via env)
LISTENER_IP = os.getenv("LISTENER_IP", "127.0.0.1")

# In-memory subscription bookkeeping (best-effort, used by create/clean functions below)
Subscriptions = []  # list of dicts: {"EP": {Host,Port,Username,Password}, "UnsubID": "..."}
RemoteMappings = []  # list of {remote_ip: host}
RemoteMapLock = threading.Lock()

# Default timeout (seconds) for HTTP requests
REQUEST_TIMEOUT = 30

parser = argparse.ArgumentParser(description="Python script using Redfish to Enable/Disable iDRAC Telemetry and all supported metric reports for one iDRAC using script arguments or multiple iDRACs using CSV file.")
parser.add_argument('--script-examples', action="store_true", help='Prints script examples')
parser.add_argument('--hosts', dest='hosts', help='Comma-delimited list of iDRAC IPs (e.g. 10.0.0.1,10.0.0.2)', required=False)
parser.add_argument('-i', '--inventory', dest='inventory', help='Path to inventory YAML (default: inventory.yaml)', default='inventory.yaml', required=False)
parser.add_argument('-u', help='iDRAC username, argument only required if configuring one or more iDRACs', required=False)
parser.add_argument('-p', help='iDRAC password, argument only required if configuring one or more iDRACs', required=False)
parser.add_argument('-s', help='Pass in the report status to be set. Possible values are Enabled/Disabled', default='Enabled', required=False)
parser.add_argument('-f', help='Pass in csv file name. If file is not located in same directory as script, pass in the full directory path with file name. NOTE: Make sure to use iDRACs.csv file from the repo which has the correct format.', required=False)
parser.add_argument('-l', '--list', action='store_true', help='Only list telemetry attribute URIs and exit', required=False)
parser.add_argument('--get-metric-report-definition', dest='get_metric_report_definition', help='Get a single MetricReportDefinition by name (e.g. MemorySensor)', required=False)
parser.add_argument('--get-metric-report', dest='get_metric_report', help='Get a single MetricReport by name (e.g. MemorySensor)', required=False)
parser.add_argument('--metric-report', dest='metric_report', help='Comma-separated MetricReportDefinition name(s) to operate on (e.g. MemorySensor,CPUSensor)', required=False)
parser.add_argument('--test', dest='test_event_id', help='Submit a test event by MessageId (e.g. Alert.1.0)', required=False)
parser.add_argument('--destination', dest='destination_url', help='Destination URL for test event (default: http://LISTENER_IP:9000)', required=False)
parser.add_argument('--event-type', dest='event_type', help='Event type for test event (default: Alert)', required=False)

args = vars(parser.parse_args())

def print_examples():
    """
    Print program examples and exit
    """
    print(
        '\n'
    )

def get_attributes(ip, user, pwd):
    """Check the current status of telemetry and return a list of telemetry attribute URIs.
    Returns:
        list: telemetry attribute URIs (strings)
    """
    # Use redfish API instead of AR
    url = 'https://{}/redfish/v1/TelemetryService/MetricReportDefinitions'.format(ip)
    headers = {'content-type': 'application/json'}
    response = requests.get(url, headers=headers, verify=False, auth=(user, pwd))
    if response.status_code != 200:
        logging.error("- FAIL, status code for reading attributes is not 200, code is: {}".format(response.status_code))
        sys.exit()
    try:
        logging.info("- INFO, successfully pulled configuration attributes")
        configurations_dict = json.loads(response.text)
        attributes = configurations_dict.get('Members', [])
        telemetry_attributes = [m['@odata.id'] for m in attributes]
        #logging.debug(telemetry_attributes)
        return telemetry_attributes
    except Exception as e:
        logging.error("- FAIL: detailed error message: {0}".format(e))
        sys.exit()


def set_attributes(ip, user, pwd, telemetry_attributes):
    """Uses the RedFish API to set the telemetry enabled attribute to user defined status.

    Args:
        telemetry_attributes (list): A list containing all telemetry attribute URIs
    """

    status_to_set = args["s"]
    if status_to_set not in ['Enabled', 'Disabled']:
        logging.error("Invalid value for report status. Supported values are Enabled & Disabled")
        sys.exit()
    headers = {'content-type': 'application/json'}
    # If user specified --metric-report, filter the telemetry attributes to only those names
    metric_arg = args.get('metric_report')
    if metric_arg:
        wanted = set([x.strip().lower() for x in metric_arg.split(',') if x.strip()])
        filtered = [uri for uri in telemetry_attributes if uri.rstrip('/').split('/')[-1].lower() in wanted]
        if not filtered:
            logging.error("- INFO, no matching MetricReportDefinitions found for %s, skipping", metric_arg)
            return
        telemetry_attributes = filtered
    
    # Enable global telemetry service    
    if status_to_set == 'Enabled':
        url = 'https://{}/redfish/v1/TelemetryService'.format(ip)
        response = requests.patch(url, data=json.dumps({"ServiceEnabled": True}), headers=headers,
                                verify=False, auth=(user, pwd), timeout=REQUEST_TIMEOUT)
        if response.status_code != 200:
            logging.error("- FAIL, status code for reading attributes is not 200, code is: {}".format(response.status_code))
            logging.debug(str(response))
            sys.exit()

    # Go to each metric report definition and enable or disable based on input
    for uri in telemetry_attributes:
        url = 'https://{}{}'.format(ip,uri)
        response = requests.patch(url, data=json.dumps({"MetricReportDefinitionEnabled": status_to_set=='Enabled'}), headers=headers,
                              verify=False, auth=(user, pwd), timeout=REQUEST_TIMEOUT)

        if response.status_code != 200:
            logging.error("- FAIL, status code for reading attributes is not 200, code is: {}".format(response.status_code))
            logging.debug(str(response))
            sys.exit()
        else:
            logging.info("- INFO, successfully set MetricReportDefinitionEnabled to {} for {}".format(status_to_set, uri))

    # Disable global telemetry service 
    if status_to_set == 'Disabled':
        url = 'https://{}/redfish/v1/TelemetryService'.format(ip)
        response = requests.patch(url, data=json.dumps({"ServiceEnabled": False}), headers=headers,
                                verify=False, auth=(user, pwd), timeout=REQUEST_TIMEOUT)

        if response.status_code != 200:
            logging.error("- FAIL, status code for reading attributes is not 200, code is: {}".format(response.status_code))
            logging.debug(str(response))
            sys.exit()
    
    logging.info("- INFO, successfully '{}' iDRAC Telemetry and all supported metric reports".format(status_to_set))


def get_metric_report_definition(ip, user, pwd, report_name):
    """Retrieve a single MetricReportDefinition by name and print JSON."""
    url = 'https://{}/redfish/v1/TelemetryService/MetricReportDefinitions/{}'.format(ip, report_name)
    headers = {'content-type': 'application/json'}
    try:
        response = requests.get(url, headers=headers, verify=False, auth=(user, pwd), timeout=REQUEST_TIMEOUT)
    except Exception as e:
        logging.error("- FAIL, error fetching MetricReportDefinition: %s", e)
        return None
    if response.status_code != 200:
        logging.error("- FAIL, status code for reading MetricReportDefinition is %s", response.status_code)
        return None
    try:
        data = json.loads(response.text)
        print(json.dumps(data, indent=2))
        return data
    except Exception as e:
        logging.error("- FAIL parsing MetricReportDefinition JSON: %s", e)
        return None


def get_metric_report(ip, user, pwd, report_name):
    """Retrieve a single MetricReport by name and print JSON."""
    url = 'https://{}/redfish/v1/TelemetryService/MetricReports/{}'.format(ip, report_name)
    headers = {'content-type': 'application/json'}
    try:
        response = requests.get(url, headers=headers, verify=False, auth=(user, pwd), timeout=REQUEST_TIMEOUT)
    except Exception as e:
        logging.error("- FAIL, error fetching MetricReport: %s", e)
        return None
    if response.status_code != 200:
        logging.error("- FAIL, status code for reading MetricReport is %s", response.status_code)
        return None
    try:
        data = json.loads(response.text)
        print(json.dumps(data, indent=2))
        return data
    except Exception as e:
        logging.error("- FAIL parsing MetricReport JSON: %s", e)
        return None


def submit_test_event(ip, user, pwd, destination_url, event_type, message_id):
    """
    Create and send a test event

    :param ip: IP address of the target iDRAC
    :param user: Username of the target iDRAC
    :param pwd: Password of the target iDRAC
    :param destination_url: The URL of the target endpoint to which you want the iDRAC logs to be sent
    :param event_type: The type of event for which you want to send data. Valid values include StatusChange,
                       ResourceUpdated, ResourceAdded, ResourceRemoved, Alert, and MetricReport.
    :param message_id: ID of the test message
    """
    payload = {
        "Destination": destination_url,
        "EventTypes": event_type,
        "Context": "Root",
        "Protocol": "Redfish",
        "MessageId": message_id,
    }
    url = "https://{}/redfish/v1/EventService/Actions/EventService.SubmitTestEvent".format(ip)
    headers = {"content-type": "application/json"}
    logging.info("Submitting test event to %s (ip=%s, type=%s, messageId=%s)", destination_url, ip, event_type, message_id)
    try:
        response = requests.post(url, data=json.dumps(payload), headers=headers, verify=False,
                                 auth=(user, pwd), timeout=REQUEST_TIMEOUT)
    except Exception as e:
        logging.error("- FAIL, request to %s failed: %s", url, e)
        sys.exit(1)
    if response.status_code == 204:
        logging.info("- PASS, POST command succeeded, status code %s returned, event type \"%s\" successfully sent to "
                     "destination \"%s\"", response.status_code, event_type, destination_url)
    else:
        logging.error("- FAIL, POST command failed, status code %s returned, error: %s",
                      response.status_code, response.text)
        sys.exit(1)


def expand_hosts_arg(hosts_arg):
    """Expand a comma-delimited hosts string into a list of host strings."""
    if not hosts_arg:
        return []
    return [h.strip() for h in hosts_arg.split(',') if h.strip()]


def load_inventory(path):
    """Load inventory YAML and extract host targets.

    Expected formats supported:
    - A list of mappings where an item contains `targets: [..]`
    - A mapping with `targets: [..]`
    Returns a list of host strings.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    hosts = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and 'targets' in item and isinstance(item['targets'], list):
                for t in item['targets']:
                    if isinstance(t, str):
                        hosts.append(t)
    elif isinstance(data, dict):
        t = data.get('targets')
        if isinstance(t, list):
            for h in t:
                if isinstance(h, str):
                    hosts.append(h)

    return hosts


def create_redfish_subscription(ip, user=None, pwd=None, port=443):
    """Create a Redfish EventService subscription on endpoint `ip`.

    Args:
        ip: host/ip string for the target iDRAC.
        user: optional username (falls back to env IDRAC_USERNAME).
        pwd: optional password (falls back to env IDRAC_PASSWORD).
        port: optional port number (default 443).

    Returns:
        unsub_id (str) on success.

    Raises:
        RuntimeError on failure.
    """
    if not ip:
        raise RuntimeError("ip/host missing")

    host = ip
    default_user = os.getenv('IDRAC_USERNAME', '')
    default_pass = os.getenv('IDRAC_PASSWORD', '')
    ssl_verify = True
    if os.getenv('IDRAC_SSL_VERIFY', '').lower() == 'false':
        ssl_verify = False

    # prefer explicit args then env defaults
    username = user if (user is not None and user != '') else default_user
    password = pwd if (pwd is not None and pwd != '') else default_pass

    ctx = host
    payload = {
        "Destination": LISTENER_IP,
        "Types": ["Alert"],
        "Context": ctx,
        "Protocol": "Redfish",
    }

    url = f"https://{host}:{port}/redfish/v1/EventService/Subscriptions"
    logging.info("Creating subscription on %s:%s -> %s (types=%s) url=%s", host, port, LISTENER_IP, payload['Types'], url)

    headers = {"Content-Type": "application/json", "Accept": "application/json"}

    auth = (username, password) if (username or password) else None

    try:
        resp = requests.post(url, json=payload, headers=headers, auth=auth, verify=ssl_verify, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        logging.error("Request to %s failed: %s", url, e)
        raise RuntimeError(f"request failed: {e}")

    body_text = resp.text or ""
    if len(body_text) > 500:
        body_text = body_text[:500] + '...'
    logging.info("Subscription response from %s: status=%s url=%s body=%r", host, resp.status_code, url, body_text)

    if resp.status_code not in (201, 204):
        raise RuntimeError(f"status {resp.status_code}: {body_text}")

    unsub_id = ""
    loc = resp.headers.get('Location', '')
    if loc:
        try:
            parsed = urllib.parse.urlparse(loc)
            path = parsed.path or loc
            if '/' in path:
                unsub_id = path.rsplit('/', 1)[-1]
            else:
                unsub_id = path
        except Exception:
            unsub_id = loc

    if not unsub_id and resp.text:
        try:
            body_map = resp.json()
            if isinstance(body_map, dict):
                v = body_map.get('Id')
                if isinstance(v, str):
                    unsub_id = v
        except Exception:
            pass

    logging.info("Successfully subscribed to %s (id=%s) status=%s url=%s", host, unsub_id, resp.status_code, url)

    # record subscription for later cleanup (store canonical EP info)
    try:
        with RemoteMapLock:
            Subscriptions.append({'EP': {'Host': host, 'Port': port, 'Username': username, 'Password': password}, 'UnsubID': unsub_id})
    except Exception:
        pass

    return unsub_id


def clean_subscriptions(user_override=None, pwd_override=None):
    """Attempt to delete created subscriptions recorded in global `Subscriptions`.

    Args:
        user_override: optional username to use for all deletes (overrides per-EP credentials)
        pwd_override: optional password to use for all deletes (overrides per-EP credentials)
    """
    env_user = os.getenv('IDRAC_USERNAME', '')
    env_pass = os.getenv('IDRAC_PASSWORD', '')
    ssl_verify = True
    if os.getenv('IDRAC_SSL_VERIFY', '').lower() == 'false':
        ssl_verify = False

    for s in list(Subscriptions):
        unsub = s.get('UnsubID') if isinstance(s, dict) else None
        ep = s.get('EP') if isinstance(s, dict) else None
        if not unsub or not ep:
            continue
        if isinstance(ep, dict):
            host = ep.get('Host')
            port = ep.get('Port', 443)
            ep_user = ep.get('Username', '')
            ep_pass = ep.get('Password', '')
        else:
            host = getattr(ep, 'Host', None)
            port = getattr(ep, 'Port', 443)
            ep_user = getattr(ep, 'Username', '')
            ep_pass = getattr(ep, 'Password', '')

        logging.info("Deleting subscription id=%s on %s:%s", unsub, host, port)

        user = user_override if (user_override is not None and user_override != '') else (ep_user or env_user)
        pwd = pwd_override if (pwd_override is not None and pwd_override != '') else (ep_pass or env_pass)

        url = f"https://{host}:{port}/redfish/v1/EventService/Subscriptions/{unsub}"

        headers = {"Content-Type": "application/json"}
        auth = (user, pwd) if user or pwd else None

        try:
            resp = requests.delete(url, headers=headers, auth=auth, verify=ssl_verify, timeout=REQUEST_TIMEOUT)
        except Exception as e:
            logging.error("Failed to delete subscription %s on %s: %s", unsub, host, e)
            continue

        body_text = resp.text or ""
        if len(body_text) > 500:
            body_text = body_text[:500] + '...'
        logging.info("Delete response from %s: status=%s body=%r", host, resp.status_code, body_text)

        if 200 <= resp.status_code < 300:
            logging.info("Deleted subscription %s on %s (status=%s)", unsub, host, resp.status_code)
            Subscriptions.remove(s)
        else:
            logging.error("Failed to delete subscription %s on %s (status=%s)", unsub, host, resp.status_code)


if __name__ == "__main__":
    if args["script_examples"]:
        print_examples()
        sys.exit(0)

    # CSV input has precedence and supports per-row credentials
    if args.get('f'):
        try:
            open_csv_file = open(args["f"], encoding='UTF8')
        except Exception:
            logging.error("\n- ERROR, unable to locate file %s" % args["f"])
            sys.exit(0)
        csv_reader = csv.reader(open_csv_file)
        next(csv_reader)
        for line in csv_reader:
            logging.info("\n- %s Telemetry attributes for iDRAC %s -\n" % (args["s"], line[0]))
            telemetry_attributes = get_attributes(line[0], line[1], line[2])
            if args.get("list"):
                for uri in telemetry_attributes:
                    print(uri)
            else:
                set_attributes(line[0], line[1], line[2], telemetry_attributes)
        sys.exit(0)

    # Determine hosts from --hosts or inventory file
    hosts_list = []
    if args.get('hosts'):
        hosts_list = expand_hosts_arg(args.get('hosts'))
    elif args.get('inventory'):
        try:
            hosts_list = load_inventory(args.get('inventory'))
        except Exception as e:
            logging.error("Failed to load inventory %s: %s", args.get('inventory'), e)
            sys.exit(1)

    if hosts_list:
        user = args.get('u') or os.getenv('IDRAC_USERNAME', '')
        pwd = args.get('p') or os.getenv('IDRAC_PASSWORD', '')
        if not (user or pwd):
            logging.error("Missing credentials: provide -u and -p or set IDRAC_USERNAME/IDRAC_PASSWORD")
            sys.exit(1)
        for host in hosts_list:
            telemetry_attributes = get_attributes(host, user, pwd)
            if args.get("list"):
                logging.info("- INFO, listing telemetry attribute URIs for %s", host)
                for uri in telemetry_attributes:
                    print(uri)
            else:
                logging.info("- INFO, setting telemetry attribute URIs for %s", host)
                set_attributes(host, user, pwd, telemetry_attributes)
        sys.exit(0)

    # Support single-host get operations via --hosts with exactly one host
    if args.get('get_metric_report_definition') or args.get('get_metric_report'):
        if not args.get('hosts'):
            logging.error("Missing host: use --hosts or -f CSV or -i inventory")
            sys.exit(1)
        hlist = expand_hosts_arg(args.get('hosts'))
        if len(hlist) != 1:
            logging.error("get-metric-report-definition/get-metric-report require exactly one host in --hosts")
            sys.exit(1)
        single = hlist[0]
        user = args.get('u') or os.getenv('IDRAC_USERNAME', '')
        pwd = args.get('p') or os.getenv('IDRAC_PASSWORD', '')
        if args.get('get_metric_report_definition'):
            get_metric_report_definition(single, user, pwd, args.get('get_metric_report_definition'))
        if args.get('get_metric_report'):
            get_metric_report(single, user, pwd, args.get('get_metric_report'))
        sys.exit(0)

    # Handle --test event submission
    if args.get('test_event_id'):
        if not args.get('hosts'):
            logging.error("Missing host: use --hosts or -f CSV or -i inventory")
            sys.exit(1)
        hlist = expand_hosts_arg(args.get('hosts'))
        if len(hlist) != 1:
            logging.error("--test requires exactly one host in --hosts")
            sys.exit(1)
        single = hlist[0]
        user = args.get('u') or os.getenv('IDRAC_USERNAME', '')
        pwd = args.get('p') or os.getenv('IDRAC_PASSWORD', '')
        destination = args.get('destination_url') or "http://{}:9000".format(LISTENER_IP)
        event_type = args.get('event_type') or 'Alert'
        submit_test_event(single, user, pwd, destination, event_type, args.get('test_event_id'))
        sys.exit(0)

    logging.warning("- WARNING, missing or incorrect arguments passed in for executing script")
    

