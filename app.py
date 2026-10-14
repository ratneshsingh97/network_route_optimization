"""Network Route Optimization API: Python standard library only, data kept in memory."""
"""Author : Ratnesh Singh"""

import heapq
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

LOCK = threading.Lock()
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

nodes = {}    # id -> name
edges = {}    # id -> {"source": name, "destination": name, "latency": float}
history = []  # oldest first
next_id = {"node": 1, "edge": 1, "history": 1}


def reset():
    nodes.clear()
    edges.clear()
    history.clear()
    next_id.update(node=1, edge=1, history=1)


class ApiError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


def text(body, key):
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ApiError(400, f"'{key}' is required")
    return value.strip()


def parse_date(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ApiError(400, "Dates must be ISO-8601")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime(TIME_FORMAT)


# ---------- endpoints ----------
def add_node(body):
    name = text(body, "name")
    if name in nodes.values():
        raise ApiError(400, f"Node '{name}' already exists")
    nid = next_id["node"]
    next_id["node"] += 1
    nodes[nid] = name
    return 201, {"id": nid, "name": name}


def list_nodes():
    return 200, [{"id": i, "name": n} for i, n in nodes.items()]


def delete_node(nid):
    if nid not in nodes:
        raise ApiError(404, "Node not found")
    name = nodes.pop(nid)
    for eid in [i for i, e in edges.items() if name in (e["source"], e["destination"])]:
        del edges[eid]
    return 204, None


def add_edge(body):
    source, destination = text(body, "source"), text(body, "destination")
    latency = body.get("latency")
    if isinstance(latency, bool) or not isinstance(latency, (int, float)) or latency <= 0:
        raise ApiError(400, "Latency must be a number greater than 0")
    if source == destination:
        raise ApiError(400, "Source and destination must differ")
    if source not in nodes.values() or destination not in nodes.values():
        raise ApiError(400, "Node not found")
    if any(e["source"] == source and e["destination"] == destination for e in edges.values()):
        raise ApiError(400, "Edge already exists")
    eid = next_id["edge"]
    next_id["edge"] += 1
    edges[eid] = {"source": source, "destination": destination, "latency": latency}
    return 201, {"id": eid, **edges[eid]}


def list_edges():
    return 200, [{"id": i, **e} for i, e in edges.items()]


def delete_edge(eid):
    if eid not in edges:
        raise ApiError(404, "Edge not found")
    del edges[eid]
    return 204, None


def shortest(body):
    source, destination = text(body, "source"), text(body, "destination")
    if source not in nodes.values() or destination not in nodes.values():
        raise ApiError(400, "Invalid or non-existent nodes")

    graph = {}
    for e in edges.values():
        graph.setdefault(e["source"], []).append((e["destination"], e["latency"]))

    dist, prev, heap = {source: 0.0}, {}, [(0.0, source)]
    while heap:
        d, node = heapq.heappop(heap)
        if d > dist[node]:
            continue
        for nxt, w in graph.get(node, []):
            if d + w < dist.get(nxt, float("inf")):
                dist[nxt], prev[nxt] = d + w, node
                heapq.heappush(heap, (d + w, nxt))

    if destination not in dist:
        raise ApiError(404, f"No path exists between {source} and {destination}")

    path = [destination]
    while path[-1] != source:
        path.append(prev[path[-1]])
    path.reverse()
    total = round(dist[destination], 6)

    hid = next_id["history"]
    next_id["history"] += 1
    history.append({
        "id": hid,
        "source": source,
        "destination": destination,
        "total_latency": total,
        "path": path,
        "created_at": datetime.now(timezone.utc).strftime(TIME_FORMAT),
    })
    return 200, {"total_latency": total, "path": path}


def get_history(q):
    try:
        limit = int(q.get("limit", 100))
    except ValueError:
        limit = 0
    if not 1 <= limit <= 1000:
        raise ApiError(400, "limit must be between 1 and 1000")
    start, end = parse_date(q.get("date_from")), parse_date(q.get("date_to"))

    rows = [
        h for h in history  # already oldest first
        if (not q.get("source") or h["source"] == q["source"])
        and (not q.get("destination") or h["destination"] == q["destination"])
        and (not start or h["created_at"] >= start)
        and (not end or h["created_at"] <= end)
    ]
    return 200, rows[:limit]


# ---------- routing + HTTP ----------
def route(method, parts, query, body):
    if parts == ["nodes"]:
        if method == "POST":
            return add_node(body)
        if method == "GET":
            return list_nodes()
    elif parts == ["edges"]:
        if method == "POST":
            return add_edge(body)
        if method == "GET":
            return list_edges()
    elif parts == ["routes", "shortest"] and method == "POST":
        return shortest(body)
    elif parts == ["routes", "history"] and method == "GET":
        return get_history(query)
    elif len(parts) == 2 and parts[0] in ("nodes", "edges") and method == "DELETE":
        if not parts[1].isdigit():
            raise ApiError(404, "Not found")
        handler = delete_node if parts[0] == "nodes" else delete_edge
        return handler(int(parts[1]))
    raise ApiError(404, "Not found")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.serve("GET")

    def do_POST(self):
        self.serve("POST")

    def do_DELETE(self):
        self.serve("DELETE")

    def serve(self, method):
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            body = {}
            if method == "POST":
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    raise ApiError(400, "Invalid JSON")
                if not isinstance(body, dict):
                    raise ApiError(400, "JSON object expected")
            with LOCK:
                status, data = route(method, parts, query, body)
        except ApiError as e:
            status, data = e.code, {"error": e.message}
        except Exception as e:
            print("error:", e)
            status, data = 500, {"error": "Internal server error"}

        payload = b"" if data is None else json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    print("Listening on http://localhost:8000")
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
