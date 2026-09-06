#!/bin/sh
set -eu

ca_path=${SSL_CERT_FILE:-}
requests_ca=${REQUESTS_CA_BUNDLE:-}

if [ -z "$ca_path" ] || [ "$ca_path" != "$requests_ca" ]; then
  echo "provider CA configuration is missing or inconsistent" >&2
  exit 1
fi
case "$ca_path" in
  /*) ;;
  *)
    echo "provider CA path must be absolute" >&2
    exit 1
    ;;
esac
if [ -L "$ca_path" ] || [ ! -f "$ca_path" ] || [ ! -r "$ca_path" ] || [ ! -s "$ca_path" ]; then
  echo "provider CA secret is unavailable or unsafe" >&2
  exit 1
fi
if [ "$#" -eq 0 ]; then
  echo "provider process command is missing" >&2
  exit 1
fi

# OpenSSL validates the PEM structure before the provider-capable process starts.
# The certificate content and path are never printed.
python3 - "$ca_path" <<'PY'
import ssl
import sys

try:
    ssl.create_default_context(cafile=sys.argv[1])
except Exception:
    raise SystemExit("provider CA secret is not a valid trust bundle") from None
PY

exec "$@"
