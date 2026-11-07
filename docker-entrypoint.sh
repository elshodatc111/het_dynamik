#!/bin/sh
set -e
python manage.py migrate --noinput
python manage.py bootstrap_admin || true
python manage.py collectstatic --noinput
exec "$@"
