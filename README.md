# Network Route Optimization API

Python standard library only. Data is kept in memory, so it is cleared when the server restarts and database is not currenly used.

## Run

    python app.py                # Runs on http://localhost:8000

## Test

    python -m unittest

## Endpoints

POST /nodes, POST /edges, POST /routes/shortest, GET /routes/history
(filters: source, destination, limit, date_from, date_to), plus
GET /nodes, GET /edges, DELETE /nodes/{id}, DELETE /edges/{id}.
Errors return `{"error": "..."}`.

## Assumptions

- Edges are directed (A -> B does not imply B -> A).
- Only successful route queries are logged. History is returned oldest first.
- Bad input returns 400 error .
