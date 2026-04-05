#!/usr/bin/env python3
"""Redfish exporter

Downloads JSON responses from a Redfish API endpoint into a matching
directory tree. For each collection or resource, the response is written
to an `index.json` file inside a folder that mirrors the resource path.

Example:
  ./redfish-exporter.py https://192.168.5.96/redfish/v1/TelemetryService/MetricReports -o output --insecure --username root --password "calvin"

Supports Basic auth (`--username/--password`) and bearer token (`--token`).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests


def build_local_path(base_output: str, url: str) -> str:
	p = urlparse(url).path.lstrip("/")
	if not p:
		p = "root"
	target_dir = os.path.join(base_output, *p.split("/"))
	return target_dir


def save_json_response(resp: requests.Response, target_dir: str) -> None:
	os.makedirs(target_dir, exist_ok=True)
	idx_path = os.path.join(target_dir, "index.json")
	try:
		data = resp.json()
	except ValueError:
		# Not JSON; write raw text
		with open(idx_path, "w", encoding="utf-8") as fh:
			fh.write(resp.text)
		return
	with open(idx_path, "w", encoding="utf-8") as fh:
		json.dump(data, fh, indent=2, sort_keys=True)


def fetch_and_save(url: str, output_dir: str, session: requests.Session, verify: bool, auth: Optional[requests.auth.AuthBase]) -> Optional[dict]:
	try:
		r = session.get(url, verify=verify, auth=auth, timeout=30)
	except requests.RequestException as e:
		print(f"ERROR: request failed for {url}: {e}")
		return None
	if r.status_code >= 400:
		print(f"ERROR: {url} returned HTTP {r.status_code}")
		return None
	target_dir = build_local_path(output_dir, url)
	save_json_response(r, target_dir)
	try:
		return r.json()
	except ValueError:
		return None


def resolve_member_url(base_url: str, member_id: str) -> str:
	if member_id.startswith("/"):
		parts = urlparse(base_url)
		base = f"{parts.scheme}://{parts.netloc}"
		return urljoin(base, member_id)
	return urljoin(base_url, member_id)


def main(argv: list[str]) -> int:
	p = argparse.ArgumentParser(description="Download Redfish JSON into folder tree")
	p.add_argument("url", help="Redfish URL to GET (collection or resource)")
	p.add_argument("-o", "--output", default="redfish-export", help="Output base directory")
	p.add_argument("--insecure", action="store_true", help="Disable TLS verification")
	p.add_argument("--username", help="Basic auth username")
	p.add_argument("--password", help="Basic auth password")
	p.add_argument("--token", help="Bearer token (Authorization: Bearer <token>)")
	args = p.parse_args(argv)

	verify = not args.insecure

	session = requests.Session()
	headers = {}
	auth = None
	if args.token:
		headers["Authorization"] = f"Bearer {args.token}"
	if args.username and args.password:
		auth = requests.auth.HTTPBasicAuth(args.username, args.password)

	session.headers.update(headers)

	base_url = args.url
	# Ensure base_url is absolute
	parsed = urlparse(base_url)
	if not parsed.scheme:
		print("ERROR: URL must include scheme, e.g. https://host/path")
		return 2

	print(f"Fetching {base_url}")
	top = fetch_and_save(base_url, args.output, session, verify, auth)
	if top is None:
		print("Failed to retrieve top-level resource; exiting")
		return 1

	# If `Members` field exists, iterate
	members = top.get("Members") if isinstance(top, dict) else None
	if isinstance(members, list):
		print(f"Found {len(members)} members; fetching each...")
		for m in members:
			member_id = None
			if isinstance(m, dict):
				member_id = m.get("@odata.id") or m.get("href")
			elif isinstance(m, str):
				member_id = m
			if not member_id:
				print("Skipping member with no @odata.id/href")
				continue
			member_url = resolve_member_url(base_url, member_id)
			print(f" - {member_url}")
			fetch_and_save(member_url, args.output, session, verify, auth)
	else:
		print("No Members list found in top-level resource.")

	print("Done.")
	return 0


if __name__ == "__main__":
	raise SystemExit(main(sys.argv[1:]))


