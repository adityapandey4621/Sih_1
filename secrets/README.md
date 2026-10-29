# Secrets for docker-compose.prod.yml

Create these files **on the server**, never in source control (this directory's other files are git-ignored). Each holds one value and nothing else.

| File | Content |
|---|---|
| `db_password` | the PostgreSQL password (long, random) |
| `database_url` | `postgresql+psycopg://sih:<the same password>@db:5432/sih_aicte` |
| `aicte_api_key` | the API key an authorised AICTE-side endpoint issued to you. If the integration is off (`AICTE_INTEGRATION_MODE=none`), put any single character. |
| `aicte_hmac_secret` | the shared signing secret for the same endpoint. As above. |

Generate random values with, for example, `openssl rand -base64 36` or `python -c "import secrets; print(secrets.token_urlsafe(36))"`.

Restrict them (`chmod 600`). Rotate by replacing the file and restarting the services; see docs/OPERATIONS.md.
