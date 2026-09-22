#!/usr/bin/env sh
set -eu

# The frozen schema describes 0_init, not future schema changes. Diff is read-only.
# Exit code 2 (drift) or 1 (error) must prevent marking anything as applied.
uv run prisma migrate diff \
    --from-schema-datasource prisma/schema.prisma \
    --to-schema-datamodel prisma/migrations/0_init/schema.prisma \
    --exit-code

# Writes only Prisma's migration ledger; never executes 0_init's CREATE statements.
uv run prisma migrate resolve --applied 0_init
