# -*- coding: utf-8 -*-
"""Google Drive primary cloud vault for MoyaIshka.

Uses the narrow drive.file OAuth scope and stores one encrypted vault file in a
visible `Moya Ishka Cloud` folder. OAuth is a desktop loopback + PKCE flow.
No Google SDK is required; networking uses the Python standard library.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
APP_FOLDER_NAME = "Moya Ishka Cloud"
VAULT_FILENAME = "moyaishka-vault.enc"
APP_UA = "MoyaIshka/4.1.4 (Google Drive vault)"


class GoogleDriveError(RuntimeError):
    """User-facing Google Drive integration error."""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _parse_json(raw: bytes, context: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise GoogleDriveError(f"Google Drive вернул некорректный ответ ({context}).") from exc


def _google_error(raw: bytes) -> str:
    try:
        obj = json.loads(raw.decode("utf-8", errors="replace"))
        if isinstance(obj, dict):
            err = obj.get("error", obj)
            if isinstance(err, dict):
                return str(err.get("message") or err.get("status") or err)[:700]
            return str(err)[:700]
    except Exception:
        pass
    return raw.decode("utf-8", errors="replace")[:500]


def authorize_desktop(client_id: str, client_secret: str, timeout: int = 240) -> dict[str, Any]:
    """Run Google OAuth in the default browser using loopback redirect + PKCE."""
    client_id = str(client_id or "").strip()
    client_secret = str(client_secret or "").strip()
    if not client_id or ".apps.googleusercontent.com" not in client_id:
        raise GoogleDriveError("Укажи OAuth Client ID типа Desktop app из Google Cloud Console.")
    if not client_secret:
        raise GoogleDriveError("Укажи Client Secret из OAuth-клиента Google.")

    state = secrets.token_urlsafe(32)
    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    callback: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlsplit(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            got_state = (params.get("state") or [""])[0]
            if got_state != state:
                callback["error"] = "Не совпал параметр безопасности OAuth state. Повтори подключение."
                status, body = 400, "Ошибка проверки авторизации. Можно закрыть это окно."
            elif params.get("error"):
                callback["error"] = (params.get("error_description") or params.get("error") or ["OAuth отменён"])[0]
                status, body = 400, "Подключение Google Drive отменено или отклонено. Можно закрыть это окно."
            else:
                callback["code"] = (params.get("code") or [""])[0]
                status, body = 200, "Google Drive подключён. Вернись в приложение «Моя ИИшка». Это окно можно закрыть."
            encoded = ("<!doctype html><meta charset='utf-8'><title>Моя ИИшка</title>"
                       "<body style='font:16px Segoe UI,Arial;background:#111827;color:#f8fafc;padding:40px'>"
                       f"<h2>{body}</h2></body>").encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_args):
            return

    try:
        server = HTTPServer(("127.0.0.1", 0), CallbackHandler)
    except OSError as exc:
        raise GoogleDriveError(f"Не удалось открыть локальный порт для Google OAuth: {exc}") from exc
    server.timeout = max(30, min(int(timeout), 300))
    redirect_uri = f"http://127.0.0.1:{server.server_port}/"
    query = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": DRIVE_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    auth_url = f"{GOOGLE_AUTH_URL}?{query}"
    try:
        opened = webbrowser.open(auth_url, new=1, autoraise=True)
        if not opened:
            raise GoogleDriveError("Не удалось открыть браузер. Проверь браузер по умолчанию и повтори подключение.")
        server.handle_request()
    finally:
        server.server_close()

    if callback.get("error"):
        raise GoogleDriveError(callback["error"])
    code = callback.get("code", "")
    if not code:
        raise GoogleDriveError("Google не вернул код авторизации за отведённое время. Повтори подключение.")

    body = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": verifier,
    }).encode("ascii")
    request = urllib.request.Request(GOOGLE_TOKEN_URL, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded", "User-Agent": APP_UA,
    })
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            token = _parse_json(response.read(1024 * 1024), "OAuth token")
    except urllib.error.HTTPError as exc:
        raise GoogleDriveError(f"Не удалось получить токен Google OAuth (HTTP {exc.code}): {_google_error(exc.read())}") from exc
    except urllib.error.URLError as exc:
        raise GoogleDriveError(f"Нет связи с Google OAuth: {exc.reason}") from exc
    if not isinstance(token, dict) or not token.get("access_token"):
        raise GoogleDriveError("Google не выдал токен доступа.")
    return {
        "access_token": str(token.get("access_token", "")),
        "refresh_token": str(token.get("refresh_token", "")),
        "expires_at": int(time.time()) + int(token.get("expires_in", 3600) or 3600),
        "token_type": str(token.get("token_type", "Bearer")),
        "scope": str(token.get("scope", DRIVE_SCOPE)),
    }


class GoogleDriveVaultClient:
    """Drive file-backed encrypted vault with a Supabase-compatible interface."""

    def __init__(self, client_id: str, client_secret: str, session: dict[str, Any] | None = None, timeout: int = 35):
        self.client_id = str(client_id or "").strip()
        self.client_secret = str(client_secret or "").strip()
        self.session = dict(session or {})
        self.timeout = max(8, min(int(timeout), 90))
        self._folder_id: str | None = None

    @staticmethod
    def authorize(client_id: str, client_secret: str) -> dict[str, Any]:
        return authorize_desktop(client_id, client_secret)

    def _token_request(self, fields: dict[str, str]) -> dict[str, Any]:
        request = urllib.request.Request(
            GOOGLE_TOKEN_URL,
            data=urllib.parse.urlencode(fields).encode("ascii"),
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": APP_UA},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = _parse_json(response.read(1024 * 1024), "token refresh")
        except urllib.error.HTTPError as exc:
            raise GoogleDriveError(f"Google OAuth отклонил запрос (HTTP {exc.code}): {_google_error(exc.read())}") from exc
        except urllib.error.URLError as exc:
            raise GoogleDriveError(f"Не удалось связаться с Google OAuth: {exc.reason}") from exc
        if not isinstance(data, dict) or not data.get("access_token"):
            raise GoogleDriveError("Google OAuth не вернул access_token.")
        self.session.update({
            "access_token": str(data.get("access_token", "")),
            "expires_at": int(time.time()) + int(data.get("expires_in", 3600) or 3600),
            "token_type": str(data.get("token_type", "Bearer")),
        })
        if data.get("refresh_token"):
            self.session["refresh_token"] = str(data["refresh_token"])
        return data

    def ensure_fresh_session(self) -> None:
        expiry = int(self.session.get("expires_at", 0) or 0)
        if self.session.get("access_token") and expiry > int(time.time()) + 90:
            return
        refresh = str(self.session.get("refresh_token", ""))
        if not refresh:
            raise GoogleDriveError("Сессия Google Drive истекла. Подключи аккаунт Google заново.")
        if not self.client_id or not self.client_secret:
            raise GoogleDriveError("В защищённой сессии не найден OAuth Client ID/Secret. Подключи Google Drive заново.")
        self._token_request({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        })

    def _api(self, method: str, path: str, *, body: bytes | None = None,
             content_type: str = "application/json", query: dict[str, str] | None = None,
             upload: bool = False) -> tuple[bytes, dict[str, str]]:
        self.ensure_fresh_session()
        base = DRIVE_UPLOAD_API if upload else DRIVE_API
        url = base + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {
            "Authorization": "Bearer " + str(self.session.get("access_token", "")),
            "User-Agent": APP_UA,
            "Accept": "application/json, application/octet-stream",
        }
        if body is not None:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read(32 * 1024 * 1024 + 1), {k.lower(): v for k, v in response.headers.items()}
        except urllib.error.HTTPError as exc:
            detail = _google_error(exc.read())
            if exc.code in {401, 403} and "insufficient" in detail.lower():
                detail += " Проверь, что в OAuth-клиенте разрешён scope drive.file и Drive API включён в Google Cloud."
            raise GoogleDriveError(f"Google Drive API: HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise GoogleDriveError(f"Google Drive временно недоступен: {exc.reason}") from exc
        except TimeoutError as exc:
            raise GoogleDriveError("Истекло время ожидания ответа Google Drive.") from exc

    def _list(self, q: str, fields: str = "files(id,name,mimeType,modifiedTime,size,md5Checksum,version)") -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        page_token = ""
        for _page in range(100):
            query = {
                "q": q,
                "spaces": "drive",
                "pageSize": "100",
                "orderBy": "modifiedTime desc",
                "fields": "nextPageToken," + fields,
            }
            if page_token:
                query["pageToken"] = page_token
            raw, _ = self._api("GET", "/files", query=query)
            data = _parse_json(raw, "files.list")
            if not isinstance(data, dict):
                raise GoogleDriveError("Google Drive вернул неверный список файлов.")
            files = data.get("files", [])
            collected.extend(item for item in files if isinstance(item, dict))
            page_token = str(data.get("nextPageToken", ""))
            if not page_token:
                return collected
        raise GoogleDriveError("В папке ИИшки слишком много страниц файлового списка; проверь архивы вручную.")

    def _folder(self) -> str:
        if self._folder_id:
            return self._folder_id
        safe_name = APP_FOLDER_NAME.replace("'", "\\'")
        matches = self._list(f"name = '{safe_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false")
        if matches:
            self._folder_id = str(matches[0]["id"])
            return self._folder_id
        metadata = _json_bytes({"name": APP_FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"})
        raw, _ = self._api("POST", "/files", body=metadata, query={"fields": "id,name"})
        result = _parse_json(raw, "create folder")
        if not isinstance(result, dict) or not result.get("id"):
            raise GoogleDriveError("Google Drive не вернул ID папки приложения.")
        self._folder_id = str(result["id"])
        return self._folder_id

    @staticmethod
    def _multipart(metadata: dict[str, Any], content: bytes) -> tuple[bytes, str]:
        boundary = "moyaishka_" + secrets.token_hex(16)
        chunks = [
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode("ascii"),
            _json_bytes(metadata),
            f"\r\n--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n".encode("ascii"),
            content,
            f"\r\n--{boundary}--\r\n".encode("ascii"),
        ]
        return b"".join(chunks), f"multipart/related; boundary={boundary}"

    def _find_vault_file(self) -> dict[str, Any] | None:
        folder = self._folder()
        safe_name = VAULT_FILENAME.replace("'", "\\'")
        rows = self._list(f"name = '{safe_name}' and '{folder}' in parents and trashed = false")
        return rows[0] if rows else None

    def get_vault(self) -> dict[str, Any] | None:
        meta = self._find_vault_file()
        if not meta:
            return None
        file_id = urllib.parse.quote(str(meta["id"]), safe="")
        raw, _ = self._api("GET", f"/files/{file_id}", query={"alt": "media"})
        data = _parse_json(raw, "vault download")
        if not isinstance(data, dict) or not all(k in data for k in ("ciphertext", "salt", "nonce", "schema_version")):
            raise GoogleDriveError("Файл облачной копии на Google Drive повреждён или имеет неизвестный формат.")
        data["updated_at"] = str(meta.get("modifiedTime", ""))
        data["file_id"] = str(meta["id"])
        data["version"] = str(meta.get("version", ""))
        data["size"] = int(meta.get("size", len(raw)) or len(raw))
        return data

    def create_vault(self, encrypted: dict[str, Any]) -> bool:
        if self._find_vault_file():
            return False
        folder = self._folder()
        metadata = {"name": VAULT_FILENAME, "parents": [folder], "mimeType": "application/octet-stream"}
        body, content_type = self._multipart(metadata, _json_bytes(encrypted))
        raw, _ = self._api("POST", "/files", body=body, content_type=content_type,
                           query={"uploadType": "multipart", "fields": "id,name,modifiedTime"}, upload=True)
        result = _parse_json(raw, "vault upload")
        return bool(isinstance(result, dict) and result.get("id"))

    def update_vault(self, encrypted: dict[str, Any], expected_updated_at: str) -> bool:
        meta = self._find_vault_file()
        if not meta:
            return self.create_vault(encrypted)
        current_revision = str(meta.get("modifiedTime", ""))
        if expected_updated_at and current_revision != expected_updated_at:
            return False
        file_id = urllib.parse.quote(str(meta["id"]), safe="")
        raw, _ = self._api("PATCH", f"/files/{file_id}", body=_json_bytes(encrypted),
                           content_type="application/json", query={"uploadType": "media", "fields": "id,name,modifiedTime"}, upload=True)
        result = _parse_json(raw, "vault update")
        return bool(isinstance(result, dict) and result.get("id"))

    def get_app_folder_usage(self) -> int:
        """Return bytes used by files in the app folder (not the whole Google account)."""
        folder = self._folder()
        items = self._list(f"'{folder}' in parents and trashed = false", fields="files(id,name,size)")
        return sum(max(0, int(item.get("size", 0) or 0)) for item in items)

    def account_email_hint(self) -> str:
        # drive.file intentionally does not request profile/email scopes.
        return "Google Drive подключён"
