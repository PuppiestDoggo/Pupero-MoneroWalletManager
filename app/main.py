from fastapi import FastAPI, Depends, HTTPException, Query, Body
from sqlmodel import Session, select
from typing import Optional, List, Dict, Any
import os
import json
import asyncio
import logging
import threading
import time
import pika
from dotenv import load_dotenv

from .database import init_db, get_session
from .models import AddressMap
from .rpc import MoneroRPC

load_dotenv()

MONERO_RPC_URL = os.getenv("MONERO_RPC_URL", "http://pupero-wallet-rpc:18083")
MONERO_RPC_USER = os.getenv("MONERO_RPC_USER", "")
MONERO_RPC_PASSWORD = os.getenv("MONERO_RPC_PASSWORD", "")
DEFAULT_ACCOUNT_INDEX = int(os.getenv("MONERO_ACCOUNT_INDEX", "0"))
MONERO_RPC_AUTH_SCHEME = os.getenv("MONERO_RPC_AUTH_SCHEME", "basic")

# RabbitMQ config
RABBITMQ_URL = os.getenv("RABBITMQ_URL")
RABBITMQ_QUEUE = os.getenv("RABBITMQ_QUEUE", "monero.transactions")
RABBITMQ_POLL_INTERVAL_SECONDS = int(os.getenv("RABBITMQ_POLL_INTERVAL_SECONDS", "1800"))

# Logger
logger = logging.getLogger("monero_wallet_manager")
if not logger.handlers:
    _h = logging.StreamHandler()
    logger.setLevel(logging.INFO)
    logger.addHandler(_h)

app = FastAPI(title="Monero Wallet Manager")


@app.on_event("startup")
async def on_startup():
    init_db()
    app.state.rpc = MoneroRPC(MONERO_RPC_URL, MONERO_RPC_USER or None, MONERO_RPC_PASSWORD or None, auth_scheme=MONERO_RPC_AUTH_SCHEME)
    # Start background consumer that polls RabbitMQ every RABBITMQ_POLL_INTERVAL_SECONDS
    try:
        if RABBITMQ_URL:
            t = threading.Thread(target=_consumer_loop, name="rabbitmq-consumer", daemon=True)
            t.start()
            logger.info(json.dumps({"event": "consumer_started", "interval_seconds": RABBITMQ_POLL_INTERVAL_SECONDS}))
        else:
            logger.info(json.dumps({"event": "consumer_disabled", "reason": "RABBITMQ_URL not set"}))
    except Exception as e:
        logger.warning(json.dumps({"event": "consumer_start_failed", "error": str(e)}))


def _rpc() -> MoneroRPC:
    return app.state.rpc  # type: ignore

# ---- RabbitMQ consumer implementation ----

def _run_async(coro):
    """Run an async coroutine in a dedicated event loop (thread-safe)."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.close()
        except Exception:
            pass
        asyncio.set_event_loop(None)


def _process_withdraw(obj: Dict[str, Any]):
    """Process a single withdraw message by calling wallet RPC transfer_split."""
    to_address = obj.get("to_address")
    amount_xmr = obj.get("amount_xmr")
    from_address = obj.get("from_address")
    if not to_address or amount_xmr is None:
        raise RuntimeError("Invalid withdraw message: missing to_address or amount_xmr")
    params: Dict[str, Any] = {
        "destinations": [{"amount": MoneroRPC.xmr_to_atomic(float(amount_xmr)), "address": to_address}],
        "get_tx_keys": True,
    }
    # Resolve from_address indices if provided
    if from_address:
        try:
            idx = _run_async(_rpc().call("get_address_index", {"address": from_address}))
            major = int(idx.get("index", {}).get("major", 0))
            minor = int(idx.get("index", {}).get("minor", 0))
            params["account_index"] = major
            params["subaddr_indices"] = [minor]
        except Exception as e:
            # If resolution fails, log and proceed without using from_address
            logger.warning(json.dumps({"event": "from_address_resolve_failed", "from_address": from_address, "error": str(e)}))
    # Optional passthrough
    for k in ["priority", "ring_size", "do_not_relay", "payment_id", "unlock_time"]:
        if k in obj:
            params[k] = obj[k]
    # Call transfer_split
    res = _run_async(_rpc().call("transfer_split", params))
    tx_hashes = res.get("tx_hash_list") or res.get("tx_hashes")
    logger.info(json.dumps({"event": "withdraw_executed", "to": to_address, "amount_xmr": amount_xmr, "tx_hash_list": tx_hashes}))


def _drain_queue_once():
    if not RABBITMQ_URL:
        return
    params = pika.URLParameters(RABBITMQ_URL)
    connection = pika.BlockingConnection(params)
    ch = connection.channel()
    ch.queue_declare(queue=RABBITMQ_QUEUE, durable=True)
    processed = 0
    try:
        while True:
            method, properties, body = ch.basic_get(queue=RABBITMQ_QUEUE, auto_ack=False)
            if method is None:
                break
            try:
                obj = json.loads(body.decode("utf-8")) if body else {}
                if obj.get("type") == "withdraw":
                    _process_withdraw(obj)
                else:
                    logger.info(json.dumps({"event": "unknown_message", "body": obj}))
                ch.basic_ack(delivery_tag=method.delivery_tag)
                processed += 1
            except Exception as e:
                logger.warning(json.dumps({"event": "message_processing_failed", "error": str(e)}))
                # Requeue the message for later
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                # Avoid rapid retries on persistent failure
                break
    finally:
        try:
            connection.close()
        except Exception:
            pass
    if processed:
        logger.info(json.dumps({"event": "queue_drain_complete", "processed": processed}))


def _consumer_loop():
    while True:
        try:
            _drain_queue_once()
        except Exception as e:
            logger.warning(json.dumps({"event": "drain_exception", "error": str(e)}))
        time.sleep(max(5, RABBITMQ_POLL_INTERVAL_SECONDS))


@app.get("/healthz")
async def healthz():
    try:
        res = await _rpc().call("get_version")
        return {"healthy": True, "version": res}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"wallet-rpc unreachable: {e}")


@app.get("/primary_address")
async def primary_address():
    try:
        # account index 0, address 0 is the primary
        res = await _rpc().call("get_address", {"account_index": 0, "address_index": [0]})
        addrs = res.get("addresses", [])
        if not addrs:
            raise RuntimeError("Wallet returned no primary address")
        return {"account_index": 0, "address_index": 0, "address": addrs[0].get("address")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/addresses")
async def list_addresses(user_id: Optional[int] = Query(default=None), session: Session = Depends(get_session)):
    stmt = select(AddressMap)
    if user_id is not None:
        stmt = stmt.where(AddressMap.user_id == user_id)
    rows = list(session.exec(stmt))
    return [
        {
            "id": r.id,
            "user_id": r.user_id,
            "address": r.address,
            "label": r.label,
            "account_index": r.account_index,
            "address_index": r.address_index,
            "created_at": r.created_at,
        } for r in rows
    ]


@app.post("/addresses")
async def create_address(payload: Dict[str, Any] = Body(...), session: Session = Depends(get_session)):
    user_id = int(payload.get("user_id")) if payload.get("user_id") is not None else None
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    label = payload.get("label")
    try:
        res = await _rpc().call("create_address", {"account_index": DEFAULT_ACCOUNT_INDEX, "label": label} if label else {"account_index": DEFAULT_ACCOUNT_INDEX})
        address = res.get("address")
        address_index = int(res.get("address_index", 0))
        if not address:
            raise RuntimeError("RPC create_address returned no address")
        row = AddressMap(user_id=user_id, address=address, label=label, account_index=DEFAULT_ACCOUNT_INDEX, address_index=address_index)
        session.add(row)
        session.commit()
        session.refresh(row)
        return {"id": row.id, "user_id": user_id, "address": address, "label": label, "account_index": DEFAULT_ACCOUNT_INDEX, "address_index": address_index}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/addresses/by-label/{label}")
async def address_by_label(label: str, session: Session = Depends(get_session)):
    stmt = select(AddressMap).where(AddressMap.label == label)
    row = session.exec(stmt).first()
    if not row:
        raise HTTPException(status_code=404, detail="label not found")
    return {"user_id": row.user_id, "address": row.address, "label": row.label, "account_index": row.account_index, "address_index": row.address_index}


@app.get("/addresses/by-address/{address}")
async def label_by_address(address: str):
    try:
        idx = await _rpc().call("get_address_index", {"address": address})
        major = int(idx.get("index", {}).get("major", 0))
        minor = int(idx.get("index", {}).get("minor", 0))
        res = await _rpc().call("get_address", {"account_index": major, "address_index": [minor]})
        arr = res.get("addresses", [])
        label = arr[0].get("label") if arr else None
        return {"address": address, "label": label, "account_index": major, "address_index": minor}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/balance/{address}")
async def balance_by_address(address: str):
    try:
        idx = await _rpc().call("get_address_index", {"address": address})
        major = int(idx.get("index", {}).get("major", 0))
        minor = int(idx.get("index", {}).get("minor", 0))
        res = await _rpc().call("get_balance", {"account_index": major, "address_indices": [minor]})
        per = res.get("per_subaddress", [])
        entry = next((p for p in per if int(p.get("address_index", -1)) == minor), None)
        if not entry:
            # Fallback: compute using overall balance if available
            bal_atomic = int(res.get("balance", 0))
            ubal_atomic = int(res.get("unlocked_balance", 0))
        else:
            bal_atomic = int(entry.get("balance", 0))
            ubal_atomic = int(entry.get("unlocked_balance", 0))
        return {
            "address": address,
            "account_index": major,
            "address_index": minor,
            "balance_atomic": bal_atomic,
            "unlocked_balance_atomic": ubal_atomic,
            "balance_xmr": MoneroRPC.atomic_to_xmr(bal_atomic),
            "unlocked_balance_xmr": MoneroRPC.atomic_to_xmr(ubal_atomic),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/balance/label/{label}")
async def balance_by_label(label: str, session: Session = Depends(get_session)):
    stmt = select(AddressMap).where(AddressMap.label == label)
    row = session.exec(stmt).first()
    if not row:
        raise HTTPException(status_code=404, detail="label not found")
    return await balance_by_address(row.address)  # re-use logic


@app.post("/transfer")
async def transfer(payload: Dict[str, Any] = Body(...)):
    to_address = payload.get("to_address")
    amount_xmr = payload.get("amount_xmr")
    if not to_address or amount_xmr is None:
        raise HTTPException(status_code=400, detail="to_address and amount_xmr are required")
    from_address = payload.get("from_address")
    params: Dict[str, Any] = {
        "destinations": [{"amount": MoneroRPC.xmr_to_atomic(float(amount_xmr)), "address": to_address}],
        "get_tx_key": True
    }
    if from_address:
        try:
            idx = await _rpc().call("get_address_index", {"address": from_address})
            major = int(idx.get("index", {}).get("major", 0))
            minor = int(idx.get("index", {}).get("minor", 0))
            params["account_index"] = major
            params["subaddr_indices"] = [minor]
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to resolve from_address: {e}")
    # Pass-through optional fields
    for k in ["priority", "ring_size", "do_not_relay", "payment_id", "unlock_time"]:
        if k in payload:
            params[k] = payload[k]
    try:
        res = await _rpc().call("transfer", params)
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/transfer_split")
async def transfer_split(payload: Dict[str, Any] = Body(...)):
    to_address = payload.get("to_address")
    amount_xmr = payload.get("amount_xmr")
    if not to_address or amount_xmr is None:
        raise HTTPException(status_code=400, detail="to_address and amount_xmr are required")
    from_address = payload.get("from_address")
    params: Dict[str, Any] = {
        "destinations": [{"amount": MoneroRPC.xmr_to_atomic(float(amount_xmr)), "address": to_address}],
        "get_tx_keys": True
    }
    if from_address:
        try:
            idx = await _rpc().call("get_address_index", {"address": from_address})
            major = int(idx.get("index", {}).get("major", 0))
            minor = int(idx.get("index", {}).get("minor", 0))
            params["account_index"] = major
            params["subaddr_indices"] = [minor]
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to resolve from_address: {e}")
    for k in ["priority", "ring_size", "do_not_relay", "payment_id", "unlock_time"]:
        if k in payload:
            params[k] = payload[k]
    try:
        res = await _rpc().call("transfer_split", params)
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/sweep_all")
async def sweep_all(payload: Dict[str, Any] = Body(...)):
    """
    Sweep all unlocked funds from a subaddress to a destination address.
    Body: { "from_address": str, "to_address"?: str, "priority"?: int, "do_not_relay"?: bool }
    Returns: { tx_hash_list, amount_list_atomic, fee_list_atomic, total_xmr }
    """
    from_address = payload.get("from_address")
    to_address = payload.get("to_address")
    if not from_address:
        raise HTTPException(status_code=400, detail="from_address is required")
    try:
        # Resolve source indices
        idx = await _rpc().call("get_address_index", {"address": from_address})
        major = int(idx.get("index", {}).get("major", 0))
        minor = int(idx.get("index", {}).get("minor", 0))
        # Resolve destination address if missing
        if not to_address:
            pa = await primary_address()
            to_address = pa.get("address")  # type: ignore
        params: Dict[str, Any] = {
            "account_index": major,
            "subaddr_indices": [minor],
            "address": to_address,
        }
        # Pass-through optional params if provided
        for k in ["priority", "do_not_relay", "ring_size", "unlock_time"]:
            if k in payload:
                params[k] = payload[k]
        res = await _rpc().call("sweep_all", params)
        tx_hash_list = res.get("tx_hash_list", [])
        amount_list = res.get("amount_list", []) or res.get("amounts", [])
        fee_list = res.get("fee_list", [])
        total_atomic = sum(int(a) for a in amount_list) if amount_list else 0
        return {
            "tx_hash_list": tx_hash_list,
            "amount_list_atomic": amount_list,
            "fee_list_atomic": fee_list,
            "total_xmr": MoneroRPC.atomic_to_xmr(total_atomic),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
