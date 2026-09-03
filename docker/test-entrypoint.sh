#!/bin/sh
# Ensure a throwaway encryption key exists for the token-store tests, without a key literal
# committed anywhere. If the caller did not supply FANTABOT_ENCRYPTION_KEY, mint a fresh Fernet
# key for this run only — it never leaves the container and guards no real credential.
set -e

if [ -z "${FANTABOT_ENCRYPTION_KEY}" ]; then
    FANTABOT_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
    export FANTABOT_ENCRYPTION_KEY
fi

exec "$@"
