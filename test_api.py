"""Tests for the Reading List API. Run with:  python3 -m unittest -v

Each test starts a fresh server on a free local port with an empty store,
so tests never depend on each other.
"""

import http.client
import json
import threading
import unittest

from app import MAX_BODY_BYTES, ReadingListServer


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.server = ReadingListServer(("127.0.0.1", 0), quiet=True)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def call(self, method, path, body=None, headers=None, raw=None):
        """Send a request; return (status, response, parsed_json_or_None)."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = dict(headers or {})
        payload = raw
        if payload is None and body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response.status, response, (json.loads(data) if data else None)

    def make(self, title="Pride and Prejudice", author="Jane Austen", **extra):
        status, _, book = self.call("POST", "/books",
                                    {"title": title, "author": author, **extra})
        self.assertEqual(status, 201)
        return book

    def assert_error(self, result, status, code, field=None):
        got_status, response, body = result
        self.assertEqual(got_status, status)
        self.assertIn("application/json", response.getheader("Content-Type"))
        self.assertEqual(set(body), {"error"})
        self.assertEqual(body["error"]["code"], code)
        self.assertIsInstance(body["error"]["message"], str)
        self.assertIsInstance(body["error"]["details"], list)
        if field:
            self.assertIn(field, [d["field"] for d in body["error"]["details"]])


class HealthTests(ApiTestCase):
    def test_health(self):
        status, _, body = self.call("GET", "/health")
        self.assertEqual((status, body), (200, {"status": "ok"}))


class CreateTests(ApiTestCase):
    def test_create_returns_201_location_and_defaults(self):
        status, response, book = self.call(
            "POST", "/books", {"title": "  Emma ", "author": "Jane Austen"})
        self.assertEqual(status, 201)
        self.assertEqual(response.getheader("Location"), "/books/1")
        self.assertEqual(book, {"id": 1, "title": "Emma", "author": "Jane Austen",
                                "status": "to_read", "rating": None, "pages": None})

    def test_create_with_all_fields(self):
        book = self.make(status="finished", rating=5, pages=432)
        self.assertEqual((book["status"], book["rating"], book["pages"]),
                         ("finished", 5, 432))

    def test_ids_increase_and_are_not_reused(self):
        first = self.make("A", "X")
        self.call("DELETE", f"/books/{first['id']}")
        second = self.make("B", "Y")
        self.assertEqual(second["id"], first["id"] + 1)

    def test_missing_required_fields(self):
        result = self.call("POST", "/books", {})
        self.assert_error(result, 422, "validation_error", "title")
        fields = [d["field"] for d in result[2]["error"]["details"]]
        self.assertEqual(sorted(fields), ["author", "title"])

    def test_wrong_types(self):
        result = self.call("POST", "/books",
                           {"title": 123, "author": ["x"], "pages": "300"})
        self.assert_error(result, 422, "validation_error")
        fields = [d["field"] for d in result[2]["error"]["details"]]
        self.assertEqual(sorted(fields), ["author", "pages", "title"])

    def test_blank_and_too_long_strings(self):
        self.assert_error(self.call("POST", "/books", {"title": "   ", "author": "A"}),
                          422, "validation_error", "title")
        self.assert_error(self.call("POST", "/books", {"title": "T", "author": "A" * 101}),
                          422, "validation_error", "author")
        self.assert_error(self.call("POST", "/books", {"title": "T" * 201, "author": "A"}),
                          422, "validation_error", "title")

    def test_invalid_status_and_numbers(self):
        self.assert_error(self.call("POST", "/books", {"title": "T", "author": "A", "status": "done"}),
                          422, "validation_error", "status")
        for rating in (0, 6, 2.5, "5", True):
            self.assert_error(
                self.call("POST", "/books", {"title": "T", "author": "A",
                                             "status": "finished", "rating": rating}),
                422, "validation_error", "rating")
        for pages in (0, 10001, -3, False):
            self.assert_error(
                self.call("POST", "/books", {"title": "T", "author": "A", "pages": pages}),
                422, "validation_error", "pages")

    def test_unknown_and_read_only_fields_rejected(self):
        self.assert_error(self.call("POST", "/books", {"title": "T", "author": "A", "id": 99}),
                          422, "validation_error", "id")
        self.assert_error(self.call("POST", "/books", {"title": "T", "author": "A", "isbn": "x"}),
                          422, "validation_error", "isbn")

    def test_rating_requires_finished(self):
        self.assert_error(
            self.call("POST", "/books", {"title": "T", "author": "A", "rating": 4}),
            422, "validation_error", "rating")

    def test_body_must_be_json_object(self):
        self.assert_error(self.call("POST", "/books", ["not", "an", "object"]),
                          422, "validation_error", "(body)")

    def test_invalid_json_is_400(self):
        self.assert_error(
            self.call("POST", "/books", raw=b"{not json",
                      headers={"Content-Type": "application/json"}),
            400, "invalid_json")

    def test_empty_body_is_400(self):
        self.assert_error(
            self.call("POST", "/books", headers={"Content-Type": "application/json"}),
            400, "bad_request")

    def test_wrong_content_type_is_415(self):
        self.assert_error(
            self.call("POST", "/books", raw=b'{"title":"T","author":"A"}',
                      headers={"Content-Type": "text/plain"}),
            415, "unsupported_media_type")

    def test_oversized_body_is_413(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.putrequest("POST", "/books")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(MAX_BODY_BYTES + 1))
        conn.endheaders()  # headers alone are enough for the server to reject
        response = conn.getresponse()
        body = json.loads(response.read())
        conn.close()
        self.assertEqual(response.status, 413)
        self.assertEqual(body["error"]["code"], "payload_too_large")

    def test_duplicate_is_409_case_insensitive(self):
        self.make("Emma", "Jane Austen")
        self.assert_error(self.call("POST", "/books", {"title": "EMMA", "author": "jane austen"}),
                          409, "conflict")


class ReadTests(ApiTestCase):
    def test_get_existing(self):
        book = self.make()
        status, _, got = self.call("GET", f"/books/{book['id']}")
        self.assertEqual((status, got), (200, book))

    def test_get_missing_is_404(self):
        self.assert_error(self.call("GET", "/books/999"), 404, "not_found")

    def test_bad_ids_are_400(self):
        for bad in ("abc", "0", "-1", "1.5"):
            self.assert_error(self.call("GET", f"/books/{bad}"), 400, "invalid_id")

    def test_list_empty(self):
        status, _, body = self.call("GET", "/books")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"data": [], "meta": {"total": 0, "limit": 20, "offset": 0}})

    def test_list_filter_by_status(self):
        self.make("A", "X")
        self.make("B", "Y", status="reading")
        _, _, body = self.call("GET", "/books?status=reading")
        self.assertEqual([b["title"] for b in body["data"]], ["B"])
        self.assertEqual(body["meta"]["total"], 1)

    def test_list_pagination(self):
        for i in range(5):
            self.make(f"Book {i}", "Author")
        _, _, body = self.call("GET", "/books?limit=2&offset=3")
        self.assertEqual([b["title"] for b in body["data"]], ["Book 3", "Book 4"])
        self.assertEqual(body["meta"], {"total": 5, "limit": 2, "offset": 3})

    def test_list_invalid_query_is_400(self):
        for query, field in (("status=nope", "status"), ("limit=0", "limit"),
                             ("limit=101", "limit"), ("limit=abc", "limit"),
                             ("offset=-1", "offset")):
            self.assert_error(self.call("GET", "/books?" + query), 400,
                              "invalid_query", field)


class ReplaceTests(ApiTestCase):
    def test_put_replaces_and_resets_optional_fields(self):
        book = self.make(status="finished", rating=4, pages=300)
        status, _, updated = self.call("PUT", f"/books/{book['id']}",
                                       {"title": "Emma", "author": "Jane Austen"})
        self.assertEqual(status, 200)
        self.assertEqual(updated, {"id": book["id"], "title": "Emma", "author": "Jane Austen",
                                   "status": "to_read", "rating": None, "pages": None})

    def test_put_requires_all_required_fields(self):
        book = self.make()
        self.assert_error(self.call("PUT", f"/books/{book['id']}", {"title": "Only title"}),
                          422, "validation_error", "author")

    def test_put_missing_is_404(self):
        self.assert_error(self.call("PUT", "/books/999", {"title": "T", "author": "A"}),
                          404, "not_found")

    def test_put_conflict_with_other_book_but_not_itself(self):
        a = self.make("A", "X")
        self.make("B", "Y")
        self.assert_error(self.call("PUT", f"/books/{a['id']}", {"title": "B", "author": "Y"}),
                          409, "conflict")
        status, _, _ = self.call("PUT", f"/books/{a['id']}",
                                 {"title": "A", "author": "X", "pages": 10})
        self.assertEqual(status, 200)


class PatchTests(ApiTestCase):
    def test_patch_changes_only_sent_fields(self):
        book = self.make(pages=100)
        status, _, updated = self.call("PATCH", f"/books/{book['id']}", {"status": "reading"})
        self.assertEqual(status, 200)
        self.assertEqual((updated["status"], updated["pages"], updated["title"]),
                         ("reading", 100, book["title"]))

    def test_patch_empty_body_is_422(self):
        book = self.make()
        self.assert_error(self.call("PATCH", f"/books/{book['id']}", {}),
                          422, "validation_error", "(body)")

    def test_patch_can_finish_and_rate(self):
        book = self.make()
        status, _, updated = self.call("PATCH", f"/books/{book['id']}",
                                       {"status": "finished", "rating": 5})
        self.assertEqual((status, updated["rating"]), (200, 5))

    def test_patch_cannot_unfinish_while_rated(self):
        book = self.make(status="finished", rating=5)
        self.assert_error(self.call("PATCH", f"/books/{book['id']}", {"status": "reading"}),
                          422, "validation_error", "rating")
        status, _, updated = self.call("PATCH", f"/books/{book['id']}",
                                       {"status": "reading", "rating": None})
        self.assertEqual((status, updated["rating"]), (200, None))

    def test_patch_null_title_rejected(self):
        book = self.make()
        self.assert_error(self.call("PATCH", f"/books/{book['id']}", {"title": None}),
                          422, "validation_error", "title")

    def test_patch_missing_is_404(self):
        self.assert_error(self.call("PATCH", "/books/999", {"status": "reading"}),
                          404, "not_found")


class DeleteTests(ApiTestCase):
    def test_delete_then_gone(self):
        book = self.make()
        status, _, body = self.call("DELETE", f"/books/{book['id']}")
        self.assertEqual((status, body), (204, None))
        self.assert_error(self.call("GET", f"/books/{book['id']}"), 404, "not_found")

    def test_delete_missing_is_404(self):
        self.assert_error(self.call("DELETE", "/books/999"), 404, "not_found")


class RoutingTests(ApiTestCase):
    def test_unknown_route_is_404(self):
        self.assert_error(self.call("GET", "/nope"), 404, "not_found")

    def test_wrong_method_is_405_with_allow_header(self):
        result = self.call("DELETE", "/books")
        self.assert_error(result, 405, "method_not_allowed")
        self.assertEqual(result[1].getheader("Allow"), "GET, POST")
        result = self.call("POST", "/books/1", {"title": "T", "author": "A"})
        self.assert_error(result, 405, "method_not_allowed")
        self.assertEqual(result[1].getheader("Allow"), "DELETE, GET, PATCH, PUT")

    def test_options_gets_json_405(self):
        self.assert_error(self.call("OPTIONS", "/books"), 405, "method_not_allowed")


if __name__ == "__main__":
    unittest.main()
