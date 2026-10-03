"""Reading List API.

A small REST API for a reading list, built with only the Python standard
library. Data lives in memory and is lost when the server stops.

Run:   python3 app.py            (serves on http://127.0.0.1:8000)
Test:  python3 -m unittest -v
"""

import argparse
import json
import re
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

MAX_BODY_BYTES = 64 * 1024
STATUSES = ("to_read", "reading", "finished")
FIELDS = ("title", "author", "status", "rating", "pages")


# --------------------------------------------------------------------------
# Errors: every failure is raised as ApiError and rendered in one format.
# --------------------------------------------------------------------------
class ApiError(Exception):
    def __init__(self, status, code, message, details=None, headers=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or []
        self.headers = headers or {}

    def body(self):
        return {"error": {"code": self.code, "message": self.message,
                          "details": self.details}}


def validation_error(details):
    return ApiError(422, "validation_error",
                    "Request body failed validation.", details)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def _check_string(field, value, max_len, problems):
    if not isinstance(value, str):
        problems.append({"field": field, "issue": "must be a string"})
        return None
    value = value.strip()
    if not value:
        problems.append({"field": field, "issue": "must not be empty"})
    elif len(value) > max_len:
        problems.append({"field": field,
                         "issue": f"must be at most {max_len} characters"})
    return value


def _check_nullable_int(field, value, low, high, problems):
    if value is None:
        return None
    # bool is a subclass of int in Python, so reject it explicitly.
    if isinstance(value, bool) or not isinstance(value, int):
        problems.append({"field": field, "issue": "must be an integer or null"})
    elif not low <= value <= high:
        problems.append({"field": field,
                         "issue": f"must be between {low} and {high}"})
    return value


def validate_book(data, require_all):
    """Check the fields that are present; return them cleaned.

    require_all=True  -> POST/PUT: title and author are mandatory.
    require_all=False -> PATCH: any subset of fields is allowed.
    """
    if not isinstance(data, dict):
        raise validation_error(
            [{"field": "(body)", "issue": "must be a JSON object"}])
    problems = []
    for key in data:
        if key not in FIELDS:
            problems.append({"field": key, "issue": "unknown or read-only field"})
    if require_all:
        for key in ("title", "author"):
            if key not in data:
                problems.append({"field": key, "issue": "is required"})

    clean = {}
    if "title" in data:
        clean["title"] = _check_string("title", data["title"], 200, problems)
    if "author" in data:
        clean["author"] = _check_string("author", data["author"], 100, problems)
    if "status" in data:
        if data["status"] in STATUSES:
            clean["status"] = data["status"]
        else:
            problems.append({"field": "status",
                             "issue": "must be one of: " + ", ".join(STATUSES)})
    if "rating" in data:
        clean["rating"] = _check_nullable_int("rating", data["rating"], 1, 5, problems)
    if "pages" in data:
        clean["pages"] = _check_nullable_int("pages", data["pages"], 1, 10000, problems)

    if problems:
        raise validation_error(problems)
    return clean


def check_rating_rule(book):
    """Cross-field rule: a rating only makes sense for a finished book."""
    if book["rating"] is not None and book["status"] != "finished":
        raise validation_error([{
            "field": "rating",
            "issue": "can only be set when status is 'finished'"}])


# --------------------------------------------------------------------------
# In-memory store
# --------------------------------------------------------------------------
class BookStore:
    def __init__(self):
        self._books = {}
        self._next_id = 1
        self._lock = threading.Lock()

    def _get_locked(self, book_id):
        book = self._books.get(book_id)
        if book is None:
            raise ApiError(404, "not_found", f"Book {book_id} not found.")
        return book

    def _check_unique(self, book, ignore_id=None):
        key = (book["title"].lower(), book["author"].lower())
        for other in self._books.values():
            if other["id"] != ignore_id and \
                    (other["title"].lower(), other["author"].lower()) == key:
                raise ApiError(
                    409, "conflict",
                    f"A book with this title and author already exists (id {other['id']}).")

    def list_books(self, status, limit, offset):
        with self._lock:
            books = [dict(b) for b in sorted(self._books.values(),
                                             key=lambda b: b["id"])]
        if status:
            books = [b for b in books if b["status"] == status]
        return books[offset:offset + limit], len(books)

    def get(self, book_id):
        with self._lock:
            return dict(self._get_locked(book_id))

    def create(self, data):
        clean = validate_book(data, require_all=True)
        book = {"title": clean["title"], "author": clean["author"],
                "status": clean.get("status", "to_read"),
                "rating": clean.get("rating"), "pages": clean.get("pages")}
        check_rating_rule(book)
        with self._lock:
            self._check_unique(book)
            stored = {"id": self._next_id, **book}
            self._next_id += 1
            self._books[stored["id"]] = stored
            return dict(stored)

    def replace(self, book_id, data):
        """PUT: full replacement. Omitted optional fields are reset."""
        with self._lock:
            self._get_locked(book_id)
            clean = validate_book(data, require_all=True)
            book = {"title": clean["title"], "author": clean["author"],
                    "status": clean.get("status", "to_read"),
                    "rating": clean.get("rating"), "pages": clean.get("pages")}
            check_rating_rule(book)
            self._check_unique(book, ignore_id=book_id)
            self._books[book_id] = {"id": book_id, **book}
            return dict(self._books[book_id])

    def update(self, book_id, data):
        """PATCH: change only the fields that were sent."""
        with self._lock:
            existing = self._get_locked(book_id)
            if isinstance(data, dict) and not data:
                raise validation_error(
                    [{"field": "(body)", "issue": "must contain at least one field"}])
            clean = validate_book(data, require_all=False)
            merged = {**existing, **clean}
            check_rating_rule(merged)
            self._check_unique(merged, ignore_id=book_id)
            self._books[book_id] = merged
            return dict(merged)

    def delete(self, book_id):
        with self._lock:
            self._get_locked(book_id)
            del self._books[book_id]


# --------------------------------------------------------------------------
# Query-string and id parsing
# --------------------------------------------------------------------------
def parse_id(raw):
    if not re.fullmatch(r"[0-9]+", raw) or int(raw) < 1:
        raise ApiError(400, "invalid_id", "Book id must be a positive integer.",
                       [{"field": "id", "issue": "must be a positive integer"}])
    return int(raw)


def _int_param(query, name, default, low, high, problems):
    if name not in query:
        return default
    raw = query[name][-1]
    if not re.fullmatch(r"[0-9]+", raw):
        problems.append({"field": name, "issue": "must be a non-negative integer"})
        return default
    value = int(raw)
    if not low <= value <= high:
        problems.append({"field": name, "issue": f"must be between {low} and {high}"})
    return value


# --------------------------------------------------------------------------
# HTTP layer
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "ReadingListAPI/1.0"

    def log_message(self, fmt, *args):
        if not self.server.quiet:
            super().log_message(fmt, *args)

    # Every method goes through one place, so unsupported methods
    # (OPTIONS, HEAD, ...) also get the standard JSON error.
    def do_GET(self): self._handle("GET")
    def do_POST(self): self._handle("POST")
    def do_PUT(self): self._handle("PUT")
    def do_PATCH(self): self._handle("PATCH")
    def do_DELETE(self): self._handle("DELETE")
    def do_HEAD(self): self._handle("HEAD")
    def do_OPTIONS(self): self._handle("OPTIONS")

    def _handle(self, method):
        try:
            status, body, headers = self._route(method)
        except ApiError as err:
            status, body, headers = err.status, err.body(), err.headers
        except Exception:  # never leak internals to the client
            traceback.print_exc(file=sys.stderr)
            err = ApiError(500, "internal_error", "An unexpected error occurred.")
            status, body, headers = 500, err.body(), {}
        self._send(status, body, headers)

    def _send(self, status, body, headers):
        payload = b"" if body is None else json.dumps(body).encode("utf-8")
        self.send_response(status)
        if body is not None:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _route(self, method):
        parts = urlsplit(self.path)
        query = parse_qs(parts.query, keep_blank_values=True)
        routes = [
            (r"/health", {"GET": self._health}),
            (r"/books", {"GET": self._list, "POST": self._create}),
            (r"/books/([^/]+)", {"GET": self._get, "PUT": self._replace,
                                 "PATCH": self._patch, "DELETE": self._delete}),
        ]
        for pattern, methods in routes:
            match = re.fullmatch(pattern, parts.path)
            if match:
                if method not in methods:
                    raise ApiError(
                        405, "method_not_allowed",
                        f"{method} is not allowed on {parts.path}.",
                        headers={"Allow": ", ".join(sorted(methods))})
                return methods[method](match, query)
        raise ApiError(404, "not_found", f"No route for {parts.path}.")

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = 0
        if length <= 0:
            raise ApiError(400, "bad_request", "A JSON request body is required.")
        if length > MAX_BODY_BYTES:
            raise ApiError(413, "payload_too_large",
                           f"Request body must be at most {MAX_BODY_BYTES} bytes.")
        raw = self.rfile.read(length)  # always consume the body first
        content_type = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type != "application/json":
            raise ApiError(415, "unsupported_media_type",
                           "Content-Type must be application/json.")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "invalid_json", "Request body is not valid JSON.")

    # ---- endpoint handlers: each returns (status, body, headers) ----------
    def _health(self, match, query):
        return 200, {"status": "ok"}, {}

    def _list(self, match, query):
        problems = []
        status = query.get("status", [None])[-1]
        if status is not None and status not in STATUSES:
            problems.append({"field": "status",
                             "issue": "must be one of: " + ", ".join(STATUSES)})
        limit = _int_param(query, "limit", 20, 1, 100, problems)
        offset = _int_param(query, "offset", 0, 0, 10**9, problems)
        if problems:
            raise ApiError(400, "invalid_query",
                           "One or more query parameters are invalid.", problems)
        books, total = self.server.store.list_books(status, limit, offset)
        return 200, {"data": books,
                     "meta": {"total": total, "limit": limit, "offset": offset}}, {}

    def _create(self, match, query):
        book = self.server.store.create(self._read_json())
        return 201, book, {"Location": f"/books/{book['id']}"}

    def _get(self, match, query):
        return 200, self.server.store.get(parse_id(match.group(1))), {}

    def _replace(self, match, query):
        book_id = parse_id(match.group(1))
        return 200, self.server.store.replace(book_id, self._read_json()), {}

    def _patch(self, match, query):
        book_id = parse_id(match.group(1))
        return 200, self.server.store.update(book_id, self._read_json()), {}

    def _delete(self, match, query):
        self.server.store.delete(parse_id(match.group(1)))
        return 204, None, {}


class ReadingListServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address=("127.0.0.1", 8000), quiet=False):
        super().__init__(address, Handler)
        self.store = BookStore()
        self.quiet = quiet


def main():
    parser = argparse.ArgumentParser(description="Reading List API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = ReadingListServer((args.host, args.port))
    print(f"Reading List API running on http://{args.host}:{args.port} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
