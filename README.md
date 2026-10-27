# AutoQuest Kenya

A responsive vehicle marketplace and multi-tenant seller portal. The included Python server uses SQLite, PBKDF2 password hashes, HttpOnly session cookies, one-time recovery codes, server-side seller accounts, and private image uploads. It uses only the Python standard library.

## Run locally

Start the application:

```sh
python3 server.py
```

Open <http://localhost:8000> for the public marketplace and <http://localhost:8000/admin.html> for the seller and admin portal. On a new installation, create the first administrator in the portal using your own name, email and password. There are no preset administrator credentials; first-admin setup is disabled once an admin account exists. Existing accounts and marketplace data are retained when the server is upgraded.

## Accounts and subscriptions

Sellers can create their own showroom login with an email and password, or an admin can invite them from **Manage tenants**. Self-registered accounts begin with a pending subscription and no seller permissions. In **Manage tenants**, an admin can approve or pause access and individually grant permission to create listings, manage their own listings (mark sold or remove), and update their WhatsApp contact. Subscription approval is separate from these permissions. Admin-invited sellers receive a temporary password and one-time recovery code; they must choose a new password on their first sign-in.

Change a password in the portal after signing in. **Forgot password?** resets the password with the one-time recovery code and issues a replacement code; save each newly issued code in a private, secure place. Seller passwords must be at least 6 characters and the initial administrator password at least 12; longer, unique passphrases are safer. The recovery flow does not rely on email delivery.

| Plan | Monthly price | Active car listings |
| --- | ---: | ---: |
| Basic Listing | KES 2,500 | 3 |
| Seller Pro | KES 7,500 | 15 |
| Enterprise | Custom | 100 |

These indicative prices come from the provided AutoQuest business blueprint. Subscription approvals are manual: M-PESA/card collection, automatic renewals and receipts are not implemented.

## Data and deployment

SQLite data and uploaded photos are stored under `./data` by default. Set `AUTOQUEST_DATA_DIR` to use a durable private storage directory. Do not delete or expose this folder.

The included server is a lightweight prototype for local review, not a hardened production hosting stack. Before going live, deploy behind a trusted HTTPS reverse proxy, set `AUTOQUEST_COOKIE_SECURE=1`, use private persistent storage and backups, and review hosting, privacy, operational monitoring, and payment-provider requirements. Keep the application on one trusted origin; cross-origin API requests are intentionally rejected.

The initial public inventory is sample content. Replace it with real listings before launch.

### Vercel static preview

This project can be deployed to Vercel as a static frontend preview only. From the repository root, use `./` as the code directory (it must be a directory, not `./index.html`), select the **Other** framework preset, and leave the build command unset. The `.vercelignore` file excludes the local database, uploads, Python runtime, and development files.

Vercel will not run `server.py` in this setup. Consequently, sign-in, seller/admin car management, and API-backed inventory/settings will not work. Use a Python host with durable persistent storage for a complete marketplace; do not treat this static preview as production.

## Tests

Run the server and tenant API tests with:

```sh
python3 -m unittest -v test_server.py
```

# jacobsite
