# gunicorn.conf.py — Gunicorn configuration for Opaux

bind = "0.0.0.0:8000"

# Single worker: required to keep APScheduler and in-memory task state consistent
workers = 1

# Multiple threads to handle concurrent requests within the single worker
threads = 4

worker_class = "gthread"

# Long timeout for AI tasks (scoring, tailoring, etc.)
timeout = 300

keepalive = 5

# Log to stdout/stderr (captured by Docker)
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Do not preload; allows create_app() factory to run fresh per worker
preload_app = False
