# Dashboards

The detector does not run inside a dashboard. It writes a case file. A dashboard is a way to read the queue.

- Solo dev and small studio: the HTML page next to each case. Open it in a browser. No cluster.
- You already run Grafana: point it at the case JSON, or at ClickHouse if the case rows live there.
- You already run Elasticsearch or Splunk: index `cases/*.json`. Do not put raw shot streams in the search cluster as the system of record. The lake is the NDJSON tree, object storage, DuckDB, or ClickHouse.

`scan-index.json` is who to pull from cold storage first (reported players). `review-index.json` is who a person reads first (the evidence).
