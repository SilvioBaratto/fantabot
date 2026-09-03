# Test/CI image — runs the full fantabot suite in Linux, where the host's Windows
# quirks (cp1252 file reads, CRLF hashes, `\` path separators) do not exist.
#
# The app itself still ships as a host-installed CLI under cron (compose has no app
# service by design); this image exists so the suite has a deterministic Linux home.
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# git: test_links / test_docs read the tracked set via `git ls-files`.
# dos2unix: the working tree is copied from a Windows checkout (CRLF); the golden
# fixture manifest hashes LF bytes, so line endings are normalised at build time.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git dos2unix \
    && rm -rf /var/lib/apt/lists/*

COPY . .

# Normalise line endings on the text the suite reads or hashes. Never touch .git.
RUN find . -type f \
        \( -name '*.py' -o -name '*.json' -o -name '*.jsonl' -o -name '*.md' -o -name '*.txt' \
           -o -name '*.sh' \) \
        -not -path './.git/*' -print0 | xargs -0 dos2unix -q

RUN pip install -e ".[dev]" \
    && chmod +x docker/test-entrypoint.sh

# The entrypoint mints a throwaway Fernet key at runtime if none is set — so no key literal
# is ever committed. Default command: the socket-free tier (needs no database); the db tier
# is opt-in via compose.
ENTRYPOINT ["./docker/test-entrypoint.sh"]
CMD ["pytest", "-q"]
