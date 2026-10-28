# chama-tracker

A REST API for a chama treasurer: track members, contributions and arrears, and collect contributions by M-Pesa through Paystack or Safaricom Daraja.

## Features

- JWT login (`/api/auth/token/`, `/api/auth/token/refresh/`)
- Chamas with a short code (used as the M-Pesa account reference), a fixed contribution and a monthly or weekly cycle. The creator becomes the chair.
- Members with roles (chair, treasurer, secretary, member). Phone numbers are normalised to `2547XXXXXXXX` / `2541XXXXXXXX`. Members are deactivated, never deleted.
- Contributions by M-Pesa, cash or bank. Only `confirmed` contributions count as paid.
- M-Pesa payment prompts through a swappable provider: Paystack (default) or Daraja. Each one has an authenticated webhook and a status-check fallback for when no webhook arrives.
- Idempotent result handling: an outcome is applied only while the row is still `pending`, under `select_for_update` in a transaction. Duplicate webhooks change nothing.
- Per-period summary: total collected, expected vs collected for the period, and each member's paid, expected, arrears, credit and last payment date.
- Django admin with filters for chamas, members and contributions.
- `seed_demo` command with a realistic six-month demo chama.

Access rules: you only see chamas where you are an active member. Other chamas return 404, not 403, so their ids don't leak. Officials (chair, treasurer, secretary) manage members. Only the treasurer records cash or bank payments. Officials can send an M-Pesa prompt to any member, and a member can send one only for themselves.

## Payment providers

Choose the provider with `PAYMENT_PROVIDER` in `.env`. Both implement the same interface in [`payments/providers/base.py`](payments/providers/base.py): `request_payment`, `check_status` and `parse_webhook`. The rest of the app never calls a provider directly. Each contribution records the provider that handled it, so switching providers doesn't strand payments that are still in flight.

**Paystack (`paystack`, the default)** is the fastest way to go live. A chama doesn't need its own paybill or a Safaricom Go-Live approval, only a Paystack account.

- The prompt goes through `POST https://api.paystack.co/charge`, with `mobile_money.provider = "mpesa"`, the amount in cents (KES × 100) and a unique reference `CH-<contribution id>-<random>`. The metadata carries `chama_id`, `member_id` and `period`.
- Paystack requires an email but members don't have one, so the app sends `<phone>@members.<short_code>.local`.
- `/charge` can answer `pay_offline`, `send_otp` and similar statuses. Its `display_text` is returned to the client as `display_text`.
- Status checks use `GET /transaction/verify/<reference>`.
- The webhook is `POST /api/pay/paystack/`. It is authenticated by the `x-paystack-signature` header, an HMAC-SHA512 of the raw body keyed with `PAYSTACK_SECRET_KEY` and compared in constant time. A mismatch gets a 401. `charge.success` and `charge.failed` are applied, and other events are acknowledged and ignored. Set this URL under *Settings → API Keys & Webhooks* in the Paystack dashboard.

**Daraja (`daraja`)** is for chamas with their own paybill or till.

- It sends an STK push straight from Safaricom: an OAuth token cached for its lifetime, a Nairobi-time timestamp, and the password `base64(shortcode + passkey + timestamp)`.
- `AccountReference` is the chama short code, and `TransactionDesc` is at most 13 characters.
- The callback is `POST /api/pay/hook/<MPESA_CALLBACK_TOKEN>/`. It deliberately leaves out the word "mpesa", because Safaricom is widely reported to drop callbacks to such URLs. A wrong token gets a 403, and a valid callback always gets `{"ResultCode": 0, "ResultDesc": "Accepted"}`.
- Status checks use `stk_query`. Its response has no receipt number, so a contribution confirmed by query gets its receipt only if the callback arrives later.

Secret keys and webhook bodies are never logged.

## Arrears rule

The logic is in [`services/arrears.py`](services/arrears.py) as pure functions with no database access.

- A **period** is a calendar month (stored as the 1st) or a week (stored as its Monday). A payment date is snapped to the start of its period.
- **expected** = `contribution_amount` × the number of periods from the member's `joined_on` period to the current period, inclusive. Someone who joins on the 19th still owes that whole month.
- **paid** = the sum of the member's `confirmed` contributions, whatever period they were for. Pending and failed rows never count.
- **arrears** = `max(expected − paid, 0)`, and **credit** = `max(paid − expected, 0)`. Paying ahead shows up as credit.
- **Deactivated members** are not expected to pay, so their arrears and credit are 0. What they paid still counts towards the chama's totals.

`GET /api/chamas/{id}/summary/?period=YYYY-MM` shows balances as they stood at the end of that period. It counts only contributions confirmed before the next period began. Weekly chamas can pass `YYYY-MM-DD` (any day in the week). With no period, the current one is used.

## Setup (WSL / Ubuntu)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then set DJANGO_SECRET_KEY and PAYSTACK_SECRET_KEY (or the MPESA_* values for Daraja)
python manage.py migrate
python manage.py seed_demo  # demo / demo1234, treasurer of "Umoja Women Group"
python manage.py createsuperuser   # optional, for /admin/
python manage.py runserver
```

SQLite is the default. For Postgres, set `DATABASE_URL=postgres://user:pass@host:5432/dbname` and install `psycopg[binary]`.

Run the tests with `pytest`. Every Paystack and Daraja HTTP call is mocked, and a real network call fails the test.

## API

| Method | Path | Who |
| --- | --- | --- |
| POST | `/api/auth/token/`, `/api/auth/token/refresh/` | anyone |
| GET, POST | `/api/chamas/` | your chamas; anyone can create one |
| GET | `/api/chamas/{id}/` | members |
| GET, POST | `/api/chamas/{id}/members/` | members read, officials write |
| GET, PATCH, DELETE | `/api/chamas/{id}/members/{member_id}/` | DELETE deactivates; officials only |
| GET | `/api/chamas/{id}/contributions/?member=&period=&status=` | members |
| POST | `/api/chamas/{id}/contributions/stk/` `{member_id, amount, period, phone?}` | officials, or a member paying for themselves |
| POST | `/api/chamas/{id}/contributions/cash/` `{member_id, amount, period, method: cash\|bank, note?}` | treasurer |
| GET | `/api/contributions/{id}/status/` | members |
| GET | `/api/chamas/{id}/summary/?period=YYYY-MM` | members |
| POST | `/api/pay/paystack/` | Paystack (signed) |
| POST | `/api/pay/hook/<MPESA_CALLBACK_TOKEN>/` | Daraja |

`period` defaults to the current period. The optional `phone` on an STK request sends the prompt to another number, for example a relative paying for the member. The response includes the provider's `display_text`.

## Testing payments locally

Neither provider needs a public URL for local testing. Poll `GET /api/contributions/{id}/status/`, and once a payment has been pending for more than `PAYMENT_STATUS_CHECK_AFTER_SECONDS` (default 20), each status read asks the provider and applies the answer.

- **Paystack:** put your `sk_test_...` key in `.env`. To receive webhooks too, expose the server (for example `ngrok http 8000`), set the webhook URL in the dashboard to `https://<ngrok-host>/api/pay/paystack/`, and add the host to `DJANGO_ALLOWED_HOSTS`.
- **Daraja:** set `PAYMENT_PROVIDER=daraja`, then add your Daraja app's consumer key and secret. The sandbox shortcode `174379` and the public sandbox passkey are already in `.env.example`. `MPESA_CALLBACK_BASE=https://example.com` is fine: Daraja needs a valid HTTPS callback URL but doesn't need to reach you. To receive real callbacks, point `MPESA_CALLBACK_BASE` at your ngrok URL.

Example login, prompt and summary:

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/auth/token/ -H 'Content-Type: application/json' \
  -d '{"username":"demo","password":"demo1234"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access"])')

# Prompt a phone to pay KES 1,000 for member 2 (the demo treasurer).
# Paystack test mode only accepts its test number 0710000000 (charged instantly);
# Daraja's sandbox uses 254708374149 or your own Safaricom number.
curl -s -X POST localhost:8000/api/chamas/1/contributions/stk/ -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"member_id": 2, "amount": 1000, "phone": "0710000000"}'

curl -s localhost:8000/api/contributions/<id>/status/ -H "Authorization: Bearer $TOKEN"
curl -s "localhost:8000/api/chamas/1/summary/?period=2026-10" -H "Authorization: Bearer $TOKEN"
```
