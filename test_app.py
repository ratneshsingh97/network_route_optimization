import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import app

server = None


def setUpModule():
    global server
    app.Handler.log_message = lambda *args: None
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def tearDownModule():
    server.shutdown()


def call(method, path, body=None, raw=None):
    url = f"http://127.0.0.1:{server.server_address[1]}{path}"
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method)
    try:
        resp = urllib.request.urlopen(req)
    except urllib.error.HTTPError as e:
        resp = e
    content = resp.read()
    return resp.status, (json.loads(content) if content else None)


class ApiTests(unittest.TestCase):
    def setUp(self):
        app.reset()

    def graph(self):
        for n in ["ServerA", "ServerB", "ServerC", "ServerD", "Isolated"]:
            call("POST", "/nodes", {"name": n})
        for s, d, w in [("ServerA", "ServerB", 12.5), ("ServerB", "ServerD", 10.9),
                        ("ServerA", "ServerD", 50), ("ServerB", "ServerC", 10.1)]:
            self.assertEqual(call("POST", "/edges", {"source": s, "destination": d, "latency": w})[0], 201)

    def test_add_node(self):
        self.assertEqual(call("POST", "/nodes", {"name": "ServerA"}), (201, {"id": 1, "name": "ServerA"}))

    def test_bad_nodes(self):
        call("POST", "/nodes", {"name": "ServerA"})
        for body in [{}, {"name": ""}, {"name": "  "}, {"name": 5}, {"name": "ServerA"}]:
            self.assertEqual(call("POST", "/nodes", body)[0], 400, body)

    def test_add_edge(self):
        call("POST", "/nodes", {"name": "A"})
        call("POST", "/nodes", {"name": "B"})
        self.assertEqual(call("POST", "/edges", {"source": "A", "destination": "B", "latency": 12.5}),
                         (201, {"id": 1, "source": "A", "destination": "B", "latency": 12.5}))
        # duplicate is rejected, the reverse direction is allowed
        self.assertEqual(call("POST", "/edges", {"source": "A", "destination": "B", "latency": 1})[0], 400)
        self.assertEqual(call("POST", "/edges", {"source": "B", "destination": "A", "latency": 1})[0], 201)

    def test_bad_edges(self):
        call("POST", "/nodes", {"name": "A"})
        call("POST", "/nodes", {"name": "B"})
        bad = [
            {"destination": "B", "latency": 1},
            {"source": "A", "latency": 1},
            {"source": "A", "destination": "B"},
            {"source": "A", "destination": "B", "latency": 0},
            {"source": "A", "destination": "B", "latency": -1},
            {"source": "A", "destination": "B", "latency": "fast"},
            {"source": "A", "destination": "A", "latency": 1},
            {"source": "A", "destination": "Ghost", "latency": 1},
        ]
        for body in bad:
            self.assertEqual(call("POST", "/edges", body)[0], 400, body)

    def test_shortest_route(self):
        self.graph()
        status, body = call("POST", "/routes/shortest", {"source": "ServerA", "destination": "ServerD"})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"total_latency": 23.4, "path": ["ServerA", "ServerB", "ServerD"]})

    def test_no_path_and_directed(self):
        self.graph()
        status, body = call("POST", "/routes/shortest", {"source": "ServerA", "destination": "Isolated"})
        self.assertEqual((status, body), (404, {"error": "No path exists between ServerA and Isolated"}))
        self.assertEqual(call("POST", "/routes/shortest", {"source": "ServerD", "destination": "ServerA"})[0], 404)

    def test_bad_route_input(self):
        self.graph()
        for body in [{}, {"source": "ServerA"}, {"source": "ServerA", "destination": "Ghost"}]:
            self.assertEqual(call("POST", "/routes/shortest", body)[0], 400, body)

    def test_history_is_oldest_first_and_only_successes(self):
        self.graph()
        call("POST", "/routes/shortest", {"source": "ServerA", "destination": "ServerD"})
        call("POST", "/routes/shortest", {"source": "ServerB", "destination": "ServerC"})
        call("POST", "/routes/shortest", {"source": "ServerA", "destination": "Isolated"})  # fails, not logged
        status, rows = call("GET", "/routes/history")
        self.assertEqual(status, 200)
        self.assertEqual([r["id"] for r in rows], [1, 2])
        self.assertEqual(rows[0]["path"], ["ServerA", "ServerB", "ServerD"])
        self.assertEqual(rows[1]["total_latency"], 10.1)
        self.assertRegex(rows[0]["created_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_history_filters(self):
        self.graph()
        call("POST", "/routes/shortest", {"source": "ServerA", "destination": "ServerD"})
        call("POST", "/routes/shortest", {"source": "ServerB", "destination": "ServerC"})
        self.assertEqual(len(call("GET", "/routes/history?source=ServerA")[1]), 1)
        self.assertEqual(len(call("GET", "/routes/history?destination=ServerC")[1]), 1)
        self.assertEqual(len(call("GET", "/routes/history?limit=1")[1]), 1)
        self.assertEqual(len(call("GET", "/routes/history?date_from=2000-01-01T00:00:00Z")[1]), 2)
        self.assertEqual(call("GET", "/routes/history?date_from=2999-01-01T00:00:00Z")[1], [])
        self.assertEqual(call("GET", "/routes/history?date_to=2000-01-01T00:00:00Z")[1], [])

    def test_bad_history_params(self):
        for qs in ["limit=0", "limit=abc", "date_from=yesterday"]:
            self.assertEqual(call("GET", "/routes/history?" + qs)[0], 400, qs)

    def test_list_and_delete(self):
        self.graph()
        self.assertEqual(len(call("GET", "/nodes")[1]), 5)
        self.assertEqual(call("GET", "/edges")[1][0],
                         {"id": 1, "source": "ServerA", "destination": "ServerB", "latency": 12.5})
        self.assertEqual(call("DELETE", "/edges/3"), (204, None))  # direct A->D
        self.assertEqual(call("DELETE", "/edges/3")[0], 404)
        self.assertEqual(call("DELETE", "/nodes/2"), (204, None))  # ServerB and its edges
        self.assertEqual(call("GET", "/edges")[1], [])
        self.assertEqual(call("DELETE", "/nodes/2")[0], 404)

    def test_unknown_route_and_bad_json(self):
        self.assertEqual(call("GET", "/nope")[0], 404)
        self.assertEqual(call("POST", "/nodes", raw=b"{oops")[0], 400)


if __name__ == "__main__":
    unittest.main()
