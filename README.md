# Reading List API

A small REST API for managing a reading list: add books, mark them as reading or finished, rate them, and delete them. Built with **only the Python standard library**, so there is nothing to install.

- Data is stored **in memory only** and disappears when the server stops.
- There are no user accounts and **no personal data**. The examples use public-domain classics.
- Full machine-readable spec: [`openapi.yaml`](openapi.yaml) (OpenAPI 3.0.3).

## Run it in five minutes

You need Python 3.9 or newer. No `pip install`, no virtual environment.

```bash
git clone https://github.com/Humairah9/aisha-reading-list-rest-api.git
cd aisha-reading-list-rest-api

python3 -m unittest -v     # run all tests (about half a second)
python3 app.py             # start the API on http://127.0.0.1:8000
```

Try it from a second terminal:

```bash
curl -i http://127.0.0.1:8000/health
```

To use another port: `python3 app.py --port 9000`.

## Endpoints

| Method | Path | What it does | Success |
|---|---|---|---|
| GET | `/health` | Check the server is up | 200 |
| GET | `/books` | List books (`?status=`, `?limit=`, `?offset=`) | 200 |
| POST | `/books` | Add a book | 201 |
| GET | `/books/{id}` | Get one book | 200 |
| PUT | `/books/{id}` | Replace a book (full update) | 200 |
| PATCH | `/books/{id}` | Change some fields of a book | 200 |
| DELETE | `/books/{id}` | Delete a book | 204 |

### A book

| Field | Type | Rules |
|---|---|---|
| `id` | integer | Assigned by the server, read-only, never reused |
| `title` | string | Required, 1 to 200 characters (whitespace is trimmed) |
| `author` | string | Required, 1 to 100 characters (whitespace is trimmed) |
| `status` | string | `to_read` (default), `reading` or `finished` |
| `rating` | integer or null | 1 to 5. Only allowed when `status` is `finished` |
| `pages` | integer or null | 1 to 10000 |

Two books cannot share the same title **and** author (compared ignoring case).
Unknown fields (including `id`) are rejected rather than silently ignored.

## Examples

These are real responses from the running server (headers trimmed to the useful ones).

**Add a book**

```bash
curl -i -X POST http://127.0.0.1:8000/books \
  -H 'Content-Type: application/json' \
  -d '{"title":"Pride and Prejudice","author":"Jane Austen","pages":432}'
```
```
HTTP/1.0 201 Created
Content-Type: application/json; charset=utf-8
Location: /books/1

{"id": 1, "title": "Pride and Prejudice", "author": "Jane Austen", "status": "to_read", "rating": null, "pages": 432}
```

**List books with a filter**

```bash
curl 'http://127.0.0.1:8000/books?status=reading'
```
```
{"data": [{"id": 2, "title": "Frankenstein", "author": "Mary Shelley", "status": "reading", "rating": null, "pages": null}], "meta": {"total": 1, "limit": 20, "offset": 0}}
```

**Get one book**

```bash
curl http://127.0.0.1:8000/books/1
```
```
{"id": 1, "title": "Pride and Prejudice", "author": "Jane Austen", "status": "to_read", "rating": null, "pages": 432}
```

**Update some fields (PATCH)**

```bash
curl -X PATCH http://127.0.0.1:8000/books/1 \
  -H 'Content-Type: application/json' \
  -d '{"status":"finished","rating":5}'
```
```
{"id": 1, "title": "Pride and Prejudice", "author": "Jane Austen", "status": "finished", "rating": 5, "pages": 432}
```

**Replace a book (PUT).** Optional fields you leave out are reset to their defaults:

```bash
curl -X PUT http://127.0.0.1:8000/books/2 \
  -H 'Content-Type: application/json' \
  -d '{"title":"Frankenstein","author":"Mary Shelley","pages":280}'
```
```
{"id": 2, "title": "Frankenstein", "author": "Mary Shelley", "status": "to_read", "rating": null, "pages": 280}
```

**Delete a book**

```bash
curl -i -X DELETE http://127.0.0.1:8000/books/2
```
```
HTTP/1.0 204 No Content
```

### Error examples

Every error uses the same shape: `{"error": {"code", "message", "details"}}`. `code` is a stable machine-readable string, `message` is for humans, and `details` lists per-field problems (empty when not applicable).

**Invalid fields (422).** All problems are reported at once:

```bash
curl -i -X POST http://127.0.0.1:8000/books \
  -H 'Content-Type: application/json' \
  -d '{"title":"","author":42,"rating":9}'
```
```
HTTP/1.0 422 Unprocessable Entity

{"error": {"code": "validation_error", "message": "Request body failed validation.", "details": [{"field": "title", "issue": "must not be empty"}, {"field": "author", "issue": "must be a string"}, {"field": "rating", "issue": "must be between 1 and 5"}]}}
```

**Duplicate (409):**

```
HTTP/1.0 409 Conflict

{"error": {"code": "conflict", "message": "A book with this title and author already exists (id 1).", "details": []}}
```

**Not found (404):**

```
HTTP/1.0 404 Not Found

{"error": {"code": "not_found", "message": "Book 2 not found.", "details": []}}
```

**Wrong method (405).** The `Allow` header lists what is permitted:

```
HTTP/1.0 405 Method Not Allowed
Allow: GET, POST

{"error": {"code": "method_not_allowed", "message": "DELETE is not allowed on /books.", "details": []}}
```

**Broken JSON (400):**

```
HTTP/1.0 400 Bad Request

{"error": {"code": "invalid_json", "message": "Request body is not valid JSON.", "details": []}}
```

## Status code choices

| Code | Used when | Why this code |
|---|---|---|
| **200 OK** | Successful GET, PUT, PATCH | The response carries the book (or list) as it now exists. |
| **201 Created** | Successful POST | A new resource exists. A `Location` header gives its URL. |
| **204 No Content** | Successful DELETE | The book is gone and there is nothing left to return. |
| **400 Bad Request** | Body missing, body is not parseable JSON, id is not a positive integer, bad query parameter | The request could not be understood at all, so there is no point checking field rules. |
| **404 Not Found** | Unknown book id or unknown path | The target does not exist. Deleting the same book twice returns 404 the second time. |
| **405 Method Not Allowed** | e.g. `DELETE /books` | The path exists but not for that method. The required `Allow` header lists valid methods. |
| **409 Conflict** | Title and author already exist | The request is valid but clashes with current state. |
| **413 Content Too Large** | Body over 64 KB | Stops oversized bodies before they are read. |
| **415 Unsupported Media Type** | `Content-Type` is not `application/json` | The server only reads JSON. |
| **422 Unprocessable Entity** | JSON parses but a field fails validation | The syntax is fine but the content is not acceptable. |
| **500 Internal Server Error** | Unexpected bug | Returns a generic message. Details go to the server log, never to the client. |

Two choices worth explaining:

- **400 vs 422.** 400 means "I could not parse this", and 422 means "I parsed it, but the values are wrong". Clients can tell a broken request from a validation problem just from the status.
- **PUT vs PATCH.** PUT replaces the whole book, so `title` and `author` are required and omitted optional fields reset. PATCH changes only what you send. To clear a value with PATCH, send `null` (for example `{"rating": null}`).

A related rule: you cannot move a rated book away from `finished` without also clearing the rating, because a rating only makes sense for a finished book. Send `{"status": "reading", "rating": null}` together.

## Project layout

```
app.py          the API: validation, in-memory store, HTTP routing
test_api.py     38 tests (each starts a real server on a free port)
openapi.yaml    OpenAPI 3.0.3 specification
.github/        CI that runs the tests on Python 3.9 and 3.12
```

## Limitations (on purpose)

- In-memory storage only: restarting the server clears all data.
- No authentication, HTTPS or rate limiting. It binds to `127.0.0.1` by default and is meant for local use and learning, not for exposing to the internet.
- Uses Python's built-in `http.server`, which is fine for a demo but not designed for production traffic.

## License

MIT. See [LICENSE](LICENSE).
