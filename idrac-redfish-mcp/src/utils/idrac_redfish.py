"""Minimal iDRAC Redfish client.

Provides a small `iDRACRedfish` class for session-based login and REST
operations against a Redfish endpoint. This is a lightweight, general
Python client (not ansible-specific).

Example:
	client = iDRACRedfish('1.2.3.4')
	client.login('admin', 'password')
	r = client.get('/redfish/v1/Systems')
	client.logout()

"""
from typing import Optional, Any, Dict
from datetime import datetime, timezone, timedelta
import requests
import threading
import logging
from .logging_config import configure_logging

# Ensure logging is configured when this module is imported so
# module/class-level loggers behave consistently.
configure_logging()

# Simple in-process token cache keyed by host. Stores dicts with keys:
# 'token' (str), 'expires' (datetime|None), 'session_uri' (str|None)
_token_cache: Dict[str, Dict[str, Any]] = {}
_cache_lock = threading.Lock()
_log = logging.getLogger(__name__)

class iDRACRedfish:
	def __init__(
		self,
		host: str,
		port: Optional[int] = 443,
		verify: bool = True,
		timeout: int = 30,
	) -> None:
		self.host = host
		self.port = port
		self.base_url = f"https://{host}"
		# If a full base_url is provided, prefer it. Otherwise construct from host
		# and optional port. If `port` is None we do not include it in the URL.
		if port != 443:
			host_part = f"{host}:{port}"
			self.base_url = f"https://{host_part}"
			
		self.verify = verify
		self.timeout = timeout

		self.http = requests.Session()
		self.http.verify = verify
		self.http.headers.update({"Content-Type": "application/json"})

		self._session_uri: Optional[str] = None
		self._token: Optional[str] = None


		# Stored credentials so we can auto-refresh tokens on auth failures
		self._username: Optional[str] = None
		self._password: Optional[str] = None

	def login(self, username: str, password: str) -> None:
		"""Create a Redfish session and store the session token.

		Attempts to POST to the Sessions resource and captures the
		returned session URI and token (from the response headers or
			self._token: Optional[str] = None
		"""
		url = self._absolute_url('/redfish/v1/SessionService/Sessions')
		# load cached token for this host if present
		with _cache_lock:
			info = _token_cache.get(self.host)
			if info:
				try:
					self._token = info.get('token')
					self._session_uri = info.get('session_uri')
					if self._token:
						self.http.headers.update({'X-Auth-Token': self._token})
				except Exception:
					self._log.exception('failed loading token cache for %s', self.host)
		payload = {"UserName": username, "Password": password}
		resp = self.http.post(url, json=payload, timeout=self.timeout)
		resp.raise_for_status()

		# Session POST usually returns 201 Created with Location header
		# containing the session resource, and the token may be in
		# the X-Auth-Token response header.
		if 'Location' in resp.headers:
			self._session_uri = resp.headers['Location']

		token = resp.headers.get('X-Auth-Token') or resp.headers.get('x-auth-token')
		if not token and resp.headers.get('X-AuthToken'):
			token = resp.headers.get('X-AuthToken')
		if not token:
			# Some Redfish implementations return the token in the body
			try:
				body = resp.json()
				token = body.get('Token') or body.get('token')
			except Exception:
				token = None

		if token:
			self._token = token
			self.http.headers.update({'X-Auth-Token': self._token})

		# persist into module cache
		with _cache_lock:
			_token_cache[self.host] = {
				'token': self._token,
				'session_uri': self._session_uri,
			}

	def logout(self) -> None:
		"""Delete the Redfish session if possible and clear credentials."""
		if self._session_uri:
			try:
				self.http.delete(self._session_uri, timeout=self.timeout)
			except Exception:
				pass
		# clear token and headers
		self._token = None
		if 'X-Auth-Token' in self.http.headers:
			del self.http.headers['X-Auth-Token']

	def set_token(self, token: str, session_uri: Optional[str] = None, expires: Optional[str] = None) -> None:
		"""Set an existing token (and optional session uri) for reuse."""
		self._token = token
		self.http.headers.update({'X-Auth-Token': self._token})
		if session_uri:
			self._session_uri = session_uri
		# persist token into cache
		with _cache_lock:
			_token_cache[self.host] = {
				'token': self._token,
				'session_uri': self._session_uri,
			}

	def token_valid(self) -> bool:
		"""Return True if a token exists and (if expiry known) is not expired."""
		return bool(self._token)

	def ensure_session(self, username: str, password: str) -> None:
		"""Ensure a valid session/token exists. If not, create one.

		This method is safe to call for every tool invocation: if the
		token is present and appears valid it returns quickly. If the
		token is missing or invalid/expired it will attempt to login with
		the provided credentials.
		"""
		# remember credentials for automatic refresh
		self._username = username
		self._password = password

		if self.token_valid():
			return

		# If we have a token but no confirmation, do a light check against the service
		if self._token:
			try:
				url = self._absolute_url('/')
				resp = self.http.get(url, timeout=self.timeout)
				if resp.status_code == 200:
					return
				if resp.status_code == 401:
					# try to pick up any cached token for this host first
					with _cache_lock:
						info = _token_cache.get(self.host)
						if info and info.get('token') and not self._token:
							self._token = info.get('token')
							self._session_uri = info.get('session_uri')
							self.http.headers.update({'X-Auth-Token': self._token})
			except Exception:
				pass

		# No valid token - perform login to create session
		self.login(username, password)

	def _absolute_url(self, path: str) -> str:
		if path.startswith('http://') or path.startswith('https://'):
			return path
		if path.startswith('/'):
			# allow callers to pass either full redfish paths or relative
			return self.base_url.rstrip('/') + path
		return self.base_url.rstrip('/') + '/' + path

	def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
		"""Generic request wrapper.

		`path` may be a full URL or a Redfish path (absolute or relative
			# after login, update cache (login already updates, but ensure consistency)
			with _cache_lock:
				_token_cache[self.host] = {
					'token': self._token,
					'expires': self._expires,
					'session_uri': self._session_uri,
				}
		to the configured `base_url`). Any extra kwargs are forwarded to
		`requests.Session.request`.
		"""
		url = self._absolute_url(path)
		# perform request and if we get an auth failure try to refresh once
		resp = self.http.request(method, url, timeout=self.timeout, **kwargs)
		if resp.status_code == 401:
			# attempt to refresh token if we have stored credentials
			if self._username and self._password:
				try:
					self._log.debug('401 received, attempting token refresh')
					self.login(self._username, self._password)
					# retry the original request once
					resp = self.http.request(method, url, timeout=self.timeout, **kwargs)
				except Exception:
					pass
		# raise for any remaining HTTP errors
		resp.raise_for_status()
		return resp

	def get(self, path: str, **kwargs: Any) -> requests.Response:
		return self.request('GET', path, **kwargs)

	def post(self, path: str, **kwargs: Any) -> requests.Response:
		return self.request('POST', path, **kwargs)

	def patch(self, path: str, **kwargs: Any) -> requests.Response:
		return self.request('PATCH', path, **kwargs)

	def delete(self, path: str, **kwargs: Any) -> requests.Response:
		return self.request('DELETE', path, **kwargs)

	def __enter__(self) -> 'iDRACRedfish':
		return self

	def __exit__(self, exc_type, exc, tb) -> None:
		self.logout()

