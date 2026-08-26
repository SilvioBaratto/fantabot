#!/usr/bin/env bash
# Dump the fantabot database to a timestamped file OUTSIDE the repository.
#
# Why this exists: docker-compose.yml mounts a named volume and that is the
# entire durability story. Today a lost volume is survivable because data/'s
# CSVs are a second copy of the reference tables — after they are deleted it is
# not. `docker compose down -v` destroys 50,634 voti rows and 50,634
# bonus_malus rows, and re-scraping them is roughly 750 GETs per season at
# REQUEST_DELAY_SECONDS = 1.0, against a site under no obligation to keep
# serving 2022/23.
#
# The dump lands in $HOME, never under the repo. It contains the league_tokens
# rows — encrypted, but still credentials — and anything inside the working tree
# is one `git add -A` away from being committed. $HOME is also a different
# volume from /Volumes/External SSD, so the dump survives unmounting the drive.
#
# To restore into a scratch database:
#
#   docker compose exec -T db psql -U postgres \
#     -c "DROP DATABASE IF EXISTS fantabot_restore; CREATE DATABASE fantabot_restore;"
#   docker compose exec -T db pg_restore -U postgres -d fantabot_restore \
#     < ~/fantabot-db-YYYYMMDD.dump
#   FANTABOT_DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:54321/fantabot_restore \
#     fantabot db-check
#
# To restore over the real database, stop anything using it first, then drop and
# recreate `fantabot` the same way. -Fc (custom format) is what makes pg_restore
# usable; a plain SQL dump would need psql and could not be restored selectively.
#
# No test covers this script: a test that shells out to `docker compose` opens a
# socket, and tests/conftest.py's autouse guard fails the default tier for that.

set -euo pipefail

OUT="${HOME}/fantabot-db-$(date +%Y%m%d).dump"

case "$(cd "$(dirname "${OUT}")" && pwd -P)" in
    /Volumes/External\ SSD/*)
        echo "refusing to write the dump onto the repo's own volume: ${OUT}" >&2
        exit 1
        ;;
esac

docker compose exec -T db pg_dump -U postgres -Fc fantabot > "${OUT}"

echo "wrote ${OUT} ($(du -h "${OUT}" | cut -f1))"
echo "restore: see the header of $0"
