MoneroWalletManager
====================

Purpose
- A FastAPI microservice that wraps monero-wallet-rpc with simple REST endpoints for managing subaddresses, balances, and transfers.
- No external Internet dependencies inside the code. It talks only to a locally running monero-wallet-rpc over HTTP JSON-RPC.

Endpoints
- GET /healthz — service health.
- GET /addresses?user_id=... — list mapped addresses for a user (from the DB).
- POST /addresses — create a new subaddress in monero-wallet-rpc and store mapping.
  - body: { "user_id": int, "label": "optional label" }
- GET /addresses/by-label/{label} — find locally stored address by label.
- GET /addresses/by-address/{address} — resolve label and indices for a given address (via RPC).
- GET /balance/{address} — balance for a given subaddress (via RPC) with atomic and XMR units.
- GET /balance/label/{label} — like above but by label (uses DB then RPC).
- POST /transfer — send XMR using monero-wallet-rpc transfer.
- POST /transfer_split — send XMR using monero-wallet-rpc transfer_split.

Environment (.env)
- DATABASE_URL=mariadb+mariadbconnector://root:mypass@127.0.0.1:3306/pupero_auth
- MONERO_RPC_URL=http://127.0.0.1:18083
- MONERO_RPC_USER=pup
- MONERO_RPC_PASSWORD=pup
- MONERO_ACCOUNT_INDEX=0
- MONERO_WALLET_MANAGER_PORT=8004

How to run locally
1) Create and activate a virtualenv in MoneroWalletManager/ and install deps:
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
2) Ensure MariaDB is up and the schema is created (use CreateDB project first if needed).
3) Start monero-wallet-rpc (see below) on localhost with RPC creds pup:pup.
4) Run the service:
   uvicorn app.main:app --host 0.0.0.0 --port 8004

Docker
- Build: docker build -t pupero-monero-wallet-manager -f MoneroWalletManager/Dockerfile .
- Run:  docker run --rm -p 8004:8004 --env-file MoneroWalletManager/.env pupero-monero-wallet-manager

Exposing monero-wallet-rpc (testnet)
We recommend running monero-wallet-rpc locally (127.0.0.1). The service connects to it using HTTP basic auth.

Prerequisites
- monero-wallet-rpc binary installed (comes with Monero CLI). On Linux it is typically named `monero-wallet-rpc`.
- A testnet daemon (monerod) or a remote testnet node.

Directory layout for wallets
- Create a local folder to store your testnet wallets, e.g. Wallets/testnet
- You can place an existing testnet wallet there (files .keys and .address.txt) or let RPC create one.

Example commands (testnet)
1) Start a testnet daemon (choose one):
   - Local node (full): monerod --testnet --data-dir /path/to/monero-data --rpc-bind-ip 127.0.0.1 --rpc-bind-port 28081 --confirm-external-bind
   - Or use a remote testnet node by pointing wallet-rpc to it with --daemon-address.

2) Start monero-wallet-rpc (testnet) on localhost with creds pup:pup:
   ~/Downloads/monero-x86_64-linux-gnu-v0.18.4.2/monero-wallet-rpc \
  --testnet \
  --rpc-bind-ip 127.0.0.1 \
  --rpc-bind-port 18083 \
  --rpc-login pup:pup \
  --wallet-file ./Pupero-WalletProject1 \
  --password vm \
  --daemon-address 127.0.0.1:28081 \
  --trusted-daemon


   Notes:
   - If you already have a wallet file, you can add: --wallet-file yourwallet --password "yourpassword"
   - Otherwise, you can first create a wallet file using the CLI `monero-wallet-cli` in testnet, then point RPC to the directory.
   - Keep RPC bound on 127.0.0.1 for security. API clients (like this service) must run on the same machine or behind a secure reverse proxy.

3) Set MoneroWalletManager .env with creds:
   MONERO_RPC_URL=http://127.0.0.1:18083
   MONERO_RPC_USER=pup
   MONERO_RPC_PASSWORD=pup

4) Verify RPC is reachable:
   curl -u pup:pup -X POST http://127.0.0.1:18083/json_rpc -d '{"jsonrpc":"2.0","id":"0","method":"get_version"}' -H 'Content-Type: application/json'

Usage examples
- Create subaddress for user 42 with label "user42-main":
  POST http://localhost:8004/addresses
  {"user_id": 42, "label": "user42-main"}

- List addresses for user 42:
  GET http://localhost:8004/addresses?user_id=42

- Get balance of address:
  GET http://localhost:8004/balance/<address>

- Transfer 0.1 XMR to an address (from any wallet output):
  POST http://localhost:8004/transfer
  {"to_address": "...", "amount_xmr": 0.1}

- Transfer 0.1 XMR from a specific subaddress:
  POST http://localhost:8004/transfer
  {"from_address": "<your subaddress>", "to_address": "<dest>", "amount_xmr": 0.1}

Security notes
- Keep monero-wallet-rpc bound to 127.0.0.1 and use strong credentials in production.
- This service forwards only the needed JSON-RPC methods and stores minimal mapping data in the DB.

Troubleshooting
- 401 Unauthorized: ensure MONERO_RPC_USER/PASSWORD match the wallet RPC --rpc-login credentials.
- Connection error: verify monero-wallet-rpc runs on the configured host/port and test with curl.
- Balance zero: on testnet it may take time to sync; ensure your daemon is synchronized and the wallet is opened.
