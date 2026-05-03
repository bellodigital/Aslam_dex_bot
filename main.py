import os
import sys
import json
import time
import threading
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Any, Optional

import requests
from flask import Flask, jsonify
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

# Solana imports (graceful fallback)
try:
    from solana.rpc.api import Client as SolanaClient
    from solana.rpc.types import TxOpts
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey as PublicKey
    from solana.rpc.commitment import Confirmed
    import base58
    SOLANA_AVAILABLE = True
except ImportError:
    SOLANA_AVAILABLE = False
    import logging as _logging
    _logging.getLogger("scalper").warning("Solana libraries not installed. Solana trades will be skipped.")

# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scalper")

# ----------------------------------------------------------------------
# Configuration (ultra‑relaxed defaults – trades WILL appear)
# ----------------------------------------------------------------------
PAPER_MODE = os.getenv("PAPER_MODE", "true").lower() == "true"
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

TARGET_CHAINS = [c.strip().lower() for c in os.getenv("TARGET_CHAINS", "bsc,base,solana,ethereum").split(",") if c.strip()]

MAX_TRADE_SIZE = float(os.getenv("MAX_TRADE_SIZE", "100.0"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "-2.5"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "3.0"))
PARTIAL_TP_PCT = float(os.getenv("PARTIAL_TP_PCT", "1.5"))
TRAILING_STOP_ENABLED = os.getenv("TRAILING_STOP_ENABLED", "true").lower() == "true"
TRAILING_ACTIVATION_PCT = float(os.getenv("TRAILING_ACTIVATION_PCT", "1.5"))
TRAILING_DISTANCE_PCT = float(os.getenv("TRAILING_DISTANCE_PCT", "0.8"))
MAX_HOLD_MINUTES = float(os.getenv("MAX_HOLD_MINUTES", "8.0"))
SLIPPAGE_PCT = float(os.getenv("SLIPPAGE_PCT", "0.3"))
SWAP_SLIPPAGE_PCT = float(os.getenv("SWAP_SLIPPAGE_PCT", "0.8"))

# --- Ultra‑relaxed filter defaults (will be used if YOU DELETE the variables) ---
MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "5000.0"))     # extremely low
MIN_VOLUME = float(os.getenv("MIN_VOLUME", "1000.0"))            # extremely low
MIN_CHANGE = float(os.getenv("MIN_CHANGE", "0.0"))               # allow even negative moment (we'll filter later)
MIN_AGE_HOURS = float(os.getenv("MIN_AGE_HOURS", "0.0"))         # no age limit
MIN_PRICE_USD = float(os.getenv("MIN_PRICE_USD", "1e-8"))        # allow dust
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "60"))
FAST_MONITOR_INTERVAL = int(os.getenv("FAST_MONITOR_INTERVAL", "5"))
MIN_SCORE = float(os.getenv("MIN_SCORE", "0.0"))                 # no score limit for now

PULLBACK_ENTRY_PCT = float(os.getenv("PULLBACK_ENTRY_PCT", "0")) # disabled
BLACKLIST_AFTER_EXIT_HOURS = float(os.getenv("BLACKLIST_AFTER_EXIT_HOURS", "4.0"))
MAX_DAILY_LOSS_USD = float(os.getenv("MAX_DAILY_LOSS_USD", "200.0"))

# ... rest of config (RPC, wallet) unchanged ...
WALLET_PRIVATE_KEY = os.getenv("WALLET_PRIVATE_KEY", "")
RPC_URLS = {
    "ethereum": os.getenv("RPC_URL_ETHEREUM", ""),
    "bsc": os.getenv("RPC_URL_BSC", ""),
    "base": os.getenv("RPC_URL_BASE", ""),
}
SOLANA_PRIVATE_KEY = os.getenv("SOLANA_PRIVATE_KEY", "")
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

# ----------------------------------------------------------------------
# Global state (same as before)
# ----------------------------------------------------------------------
active_trades: Dict[str, dict] = {}
recent: Dict[str, float] = {}
trade_lock = threading.Lock()
scan_cycle_count = 0
exit_blacklist: Dict[str, float] = {}

web3_instances: Dict[str, Web3] = {}
wallet_account = None

solana_client: Optional[Any] = None
solana_keypair: Optional[Any] = None
SOL_MINT = "So11111111111111111111111111111111111111112"
momentum_history: Dict[str, list] = {}

tp_hits = 0
sl_hits = 0
total_exits = 0
daily_pnl_usd = 0.0
daily_reset_time = time.time()
halt_trading = False

# ----------------------------------------------------------------------
# Discord & Web3 helpers (unchanged, keep from previous full code)
# ... (all functions like get_web3, execute_swap_evm, etc. remain identical) ...
# To keep this response concise, I'm not repeating them.  Use the latest full code.
# ----------------------------------------------------------------------

# Include all previously defined functions: send_discord_alert, get_web3, execute_swap_evm, Solana helpers, API fetchers, etc.
# (Copy them from the last full code I provided.)

# For brevity, I'll skip pasting them here, but they MUST be in your final file.
# ----------------------------------------------------------------------
# The critical new part: filter_pairs with diagnostic counts
# ----------------------------------------------------------------------
def filter_pairs(pairs: List[dict]) -> List[dict]:
    now_ms = int(time.time() * 1000)
    valid = []
    # Rejection counters
    rejects = {
        "chain": 0, "price": 0, "liq": 0, "vol": 0, "m5": 0,
        "age": 0, "m1_neg": 0, "vol_accel": 0
    }
    for pair in pairs:
        try:
            chain = pair.get("chainId")
            if chain not in TARGET_CHAINS:
                rejects["chain"] += 1
                continue
            price = float(pair.get("priceUsd", 0))
            if price < MIN_PRICE_USD:
                rejects["price"] += 1
                continue
            liq = float(pair.get("liquidity", {}).get("usd", 0))
            if liq < MIN_LIQUIDITY:
                rejects["liq"] += 1
                continue
            vol = float(pair.get("volume", {}).get("h24", 0))
            if vol < MIN_VOLUME:
                rejects["vol"] += 1
                continue
            m5 = float(pair.get("priceChange", {}).get("m5", 0))
            if m5 < MIN_CHANGE:
                rejects["m5"] += 1
                continue
            created = int(pair.get("pairCreatedAt", 0))
            age_hours = (now_ms - created) / 3600000 if created else 0
            if MIN_AGE_HOURS > 0 and age_hours < MIN_AGE_HOURS:
                rejects["age"] += 1
                continue
            m1 = float(pair.get("priceChange", {}).get("m1", 0))
            if m1 <= 0:                     # still require positive 1-min momentum
                rejects["m1_neg"] += 1
                continue

            # Volume acceleration (m5 volume vs h1 average) – DISABLED for now, just logs
            vol_m5 = float(pair.get("volume", {}).get("m5", 0))
            vol_h1 = float(pair.get("volume", {}).get("h1", 0))
            if vol_h1 > 0 and vol_m5 < (vol_h1 / 12) * 1.5:
                rejects["vol_accel"] += 1
                # continue   <-- COMMENTED OUT to allow trades
                # We'll still count it but not reject
                pass

            pair["_liq"] = liq
            pair["_vol"] = vol
            pair["_m5"] = m5
            pair["_m1"] = m1
            pair["_age_hours"] = age_hours
            pair["_price"] = price
            pair["_chain"] = chain
            valid.append(pair)
        except:
            continue

    # Print rejection summary
    logger.info(f"Filter rejection counts: {rejects}")
    logger.info(f"Filter summary: {len(pairs)} input, {len(valid)} passed for {TARGET_CHAINS}")
    return valid

# ----------------------------------------------------------------------
# Rest of the code: is_pullback_entry (disabled), confirm_momentum, calculate_pair_score, etc.
# Use exactly the same functions from the latest full v12 code, but with the relaxed defaults.
# ----------------------------------------------------------------------
# (Copy all remaining functions from the previous v12 relaxed code.)
# For brevity, I'll not paste them, but they must be included unchanged except filter_pairs above.
# ----------------------------------------------------------------------

# At the very bottom, the entry point and Flask app remain the same.
if __name__ == "__main__":
    # ... same as before
