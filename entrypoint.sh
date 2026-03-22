#!/bin/bash
set -e

echo "Starting Opaux..."

# Run create_app() to trigger DB initialisation and any startup checks
# before handing control to gunicorn.
python -c "from web.app import create_app; create_app()"

exec gunicorn --config gunicorn.conf.py "web.app:create_app()"
