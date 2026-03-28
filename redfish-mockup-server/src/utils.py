import json
import os
import random
from datetime import datetime


def load_json_file(path):
    """Load and return JSON content from `path`.

    Raises the underlying file/JSON exceptions to the caller.
    """
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def update_created_dates(data, today=None):
    """If `data` contains a Members list, replace the date portion of
    each member's `Created` field with today's date (keep time/tz).
    """
    if today is None:
        today = datetime.now().strftime('%Y-%m-%d')

    members = data.get('Members', [])
    for m in members:
        created = m.get('Created')
        if created and isinstance(created, str) and len(created) > 10:
            m['Created'] = today + created[10:]

    return data


def rewrite_ids_random(event):
    """Rewrite common `Id` fields in the event to a random numeric string.

    Works in-place and returns the event for convenience.
    """
    if not isinstance(event, dict):
        return event

    if 'Id' in event:
        event['Id'] = str(random.randint(1, 99999))

    if 'Events' in event and isinstance(event['Events'], list):
        for e in event['Events']:
            if isinstance(e, dict) and 'Id' in e:
                e['Id'] = str(random.randint(1, 99999))

    if 'Members' in event and isinstance(event['Members'], list):
        for m in event['Members']:
            if isinstance(m, dict) and 'Id' in m:
                m['Id'] = str(random.randint(1, 99999))

    return event


def modify_first_log(event):
    """For Event/collection payloads, change the first log's fields for testing.

    - Set `Severity` to "Critical"
    - Set `Message` to a CPU thermal trip text
    - Set `MessageId` to "IDRAC.2.13.CPU0001"
    """
    if not isinstance(event, dict):
        return event

    cpu_message = "CPU 1 has a thermal trip (over-temperature) event."
    cpu_message_id = "IDRAC.2.13.CPU0001"

    if 'Members' in event and isinstance(event['Members'], list) and len(event['Members']) > 0:
        first = event['Members'][0]
        if isinstance(first, dict):
            first['Severity'] = 'Critical'
            first['Message'] = cpu_message
            first['MessageId'] = cpu_message_id
    else:
        if 'Severity' in event:
            event['Severity'] = 'Critical'
        if 'Message' in event:
            event['Message'] = cpu_message
        if 'MessageId' in event:
            event['MessageId'] = cpu_message_id

    return event


def parse_top_skip(query_params):
    """Return ($top, $skip) as (int|None, int|None) from request query params.

    Accepts any mapping-like with .get (e.g. Request.query_params).
    Invalid integers are treated as None.
    """
    top = None
    skip = None
    t = query_params.get('$top')
    s = query_params.get('$skip')
    try:
        if t is not None:
            top = int(t)
            if top < 0:
                top = None
    except Exception:
        top = None

    try:
        if s is not None:
            skip = int(s)
            if skip < 0:
                skip = None
    except Exception:
        skip = None

    return top, skip


def apply_top_skip(data, top, skip):
    """Apply $top/$skip paging to `data['Members']` if present.

    The filtering is applied after all other modifications. The function
    updates `Members` in-place and sets/updates `Members@odata.count`
    to the returned members length.
    """
    if not isinstance(data, dict):
        return data

    members = data.get('Members')
    if not isinstance(members, list):
        return data

    start = skip or 0
    if top is None:
        end = None
    else:
        end = start + top

    sliced = members[start:end]
    data['Members'] = sliced

    # Update count to the number of returned members
    data['Members@odata.count'] = len(sliced)

    return data
