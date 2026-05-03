# =============================================================================
# DEX SCALPER PRO v4 - OPTIMIZED FOR 70%+ TAKE PROFIT RATE
# Strategy: Quality > Quantity. Fewer trades, higher conviction, smarter exits.
# =============================================================================

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
    _logging.getLogger("scalper").warning("Solana libraries not installed.")

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
# CONFIG — Tuned for 70%+ TP rate
# KEY PHILOSOPHY:
#   1. Only trade tokens that are ALREADY moving with confirmed momentum
#   2. Lock profit fast (partial TP at +2%), never let winners turn losers
#   3. Tight SL (-2%) but trailing stop preserves gains
#   4. Security check mandatory — no honeypots, ever
#   5. Age filter stops rug-pull micro-caps
# ----------------------------------------------------------------------

PAPER_MODE = os.getenv("PAPER_MODE", "true").lower() == "true"
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# Chain targets — BSC and Base have best liquidity/speed for scalping
TARGET_CHAINS = [c.strip().lower() for c in os.getenv("TARGET_CHAINS", "bsc,base").split(",") if c.strip()]

# === POSITION SIZING ===
MAX_TRADE_SIZE = float(os.getenv("MAX_TRADE_SIZE", "100.0"))
MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES", "3"))  # NEW: cap concurrent positions

# === EXIT STRATEGY — The core of 70% win rate ===
TAKE_PROFIT_PCT      = float(os.getenv("TAKE_PROFIT_PCT", "4.0"))       # Full TP target
PARTIAL_TP_PCT       = float(os.getenv("PARTIAL_TP_PCT", "2.0"))        # Sell 50% here fast
PARTIAL_TP_RATIO     = float(os.getenv("PARTIAL_TP_RATIO", "0.5"))      # How much to sell at partial TP
STOP_LOSS_PCT        = float(os.getenv("STOP_LOSS_PCT", "-2.0"))        # Hard SL — tight!
EMERGENCY_STOP_PCT   = float(os.getenv("EMERGENCY_STOP_PCT", "-4.0"))   # Absolute floor

# === TRAILING STOP — Locks profits after partial TP ===
TRAILING_STOP_ENABLED       = os.getenv("TRAILING_STOP_ENABLED", "true").lower() == "true"
TRAILING_ACTIVATION_PCT     = float(os.getenv("TRAILING_ACTIVATION_PCT", "2.0"))  # Activate after +2%
TRAILING_DISTANCE_PCT       = float(os.getenv("TRAILING_DISTANCE_PCT", "1.0"))    # Trail by 1%

# === TIME LIMIT ===
MAX_HOLD_MINUTES     = float(os.getenv("MAX_HOLD_MINUTES", "12.0"))     # Exit if no move in 12 min

# === ENTRY FILTERS — Strict quality gate ===
MIN_LIQUIDITY        = float(os.getenv("MIN_LIQUIDITY", "200000.0"))    # $200k min liq
MIN_VOLUME           = float(os.getenv("MIN_VOLUME", "50000.0"))        # $50k 24h volume
MIN_CHANGE           = float(os.getenv("MIN_CHANGE", "3.0"))            # m5 must be +3%+ 
MIN_M1_CHANGE        = float(os.getenv("MIN_M1_CHANGE", "0.5"))         # m1 positive required
MAX_M1_CHANGE        = float(os.getenv("MAX_M1_CHANGE", "8.0"))         # Avoid buying tops
MIN_AGE_HOURS        = float(os.getenv("MIN_AGE_HOURS", "1.0"))         # 1hr min age — avoid rugs
MAX_AGE_HOURS        = float(os.getenv("MAX_AGE_HOURS", "72.0"))        # 3 days max — need fresh momentum
MIN_PRICE_USD        = float(os.getenv("MIN_PRICE_USD", "0.000001"))
MAX_SELL_TAX         = float(os.getenv("MAX_SELL_TAX", "8.0"))          # Reject high tax tokens
MAX_BUY_TAX          = float(os.getenv("MAX_BUY_TAX", "8.0"))

# Volume acceleration: m5 vol must be > this multiple of average 5-min slice
VOL_ACCEL_MULTIPLIER = float(os.getenv("VOL_ACCEL_MULTIPLIER", "2.0"))  # 2x avg = real spike

# === TIMING ===
SCAN_INTERVAL_SECONDS   = int(os.getenv("SCAN_INTERVAL_SECONDS", "30"))  # Faster scans
FAST_MONITOR_INTERVAL   = int(os.getenv("FAST_MONITOR_INTERVAL", "3"))   # Tighter monitor

# === SCORE THRESHOLD ===
MIN_SCORE = float(os.getenv("MIN_SCORE", "75.0"))  # Slightly relaxed but better scoring

# === MOMENTUM CONFIRMATION ===
MOMENTUM_REQUIRED = int(os.getenv("MOMENTUM_REQUIRED", "2"))  # 2 consecutive positive m1 reads

# === RISK MANAGEMENT ===
BLACKLIST_AFTER_EXIT_HOURS  = float(os.getenv("BLACKLIST_AFTER_EXIT_HOURS", "6.0"))
MAX_DAILY_LOSS_USD          = float(os.getenv("MAX_DAILY_LOSS_USD", "200.0"))

# === SLIPPAGE ===
SLIPPAGE_PCT        = float(os.getenv("SLIPPAGE_PCT", "0.3"))
SWAP_SLIPPAGE_PCT   = float(os.getenv("SWAP_SLIPPAGE_PCT", "1.0"))

# === WALLET / RPC ===
WALLET_PRIVATE_KEY = os.getenv("WALLET_PRIVATE_KEY", "")
RPC_URLS = {
    "ethereum": os.getenv("RPC_URL_ETHEREUM", ""),
    "bsc":      os.getenv("RPC_URL_BSC", ""),
    "base":     os.getenv("RPC_URL_BASE", ""),
}
SOLANA_PRIVATE_KEY = os.getenv("SOLANA_PRIVATE_KEY", "")
SOLANA_RPC_URL     = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

JUPITER_QUOTE_API = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_API  = "https://quote-api.jup.ag/v6/swap"

CHAIN_ID_MAP = {
    "bsc": 56, "ethereum": 1, "polygon": 137, "avalanche": 43114,
    "fantom": 250, "arbitrum": 42161, "optimism": 10, "base": 8453, "solana": 101,
}
ROUTER_ADDRESSES = {
    "ethereum": "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
    "bsc":      "0x10ED43C718714eb63d5aA57B78B54704E256024E",
    "base":     "0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24",
}
WETH_ADDRESSES = {
    "ethereum": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
    "bsc":      "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
    "base":     "0x4200000000000000000000000000000000000006",
}

ROUTER_ABI = json.loads("""[
  {"inputs":[{"internalType":"uint256","name":"amountOutMin","type":"uint256"},{"internalType":"address[]","name":"path","type":"address[]"},{"internalType":"address","name":"to","type":"address"},{"internalType":"uint256","name":"deadline","type":"uint256"}],"name":"swapExactETHForTokens","outputs":[{"internalType":"uint256[]","name":"amounts","type":"uint256[]"}],"stateMutability":"payable","type":"function"},
  {"inputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"uint256","name":"amountOutMin","type":"uint256"},{"internalType":"address[]","name":"path","type":"address[]"},{"internalType":"address","name":"to","type":"address"},{"internalType":"uint256","name":"deadline","type":"uint256"}],"name":"swapExactTokensForETH","outputs":[{"internalType":"uint256[]","name":"amounts","type":"uint256[]"}],"stateMutability":"nonpayable","type":"function"},
  {"inputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"uint256","name":"amountOutMin","type":"uint256"},{"internalType":"address[]","name":"path","type":"address[]"},{"internalType":"address","name":"to","type":"address"},{"internalType":"uint256","name":"deadline","type":"uint256"}],"name":"swapExactTokensForTokens","outputs":[{"internalType":"uint256[]","name":"amounts","type":"uint256[]"}],"stateMutability":"nonpayable","type":"function"},
  {"inputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"address[]","name":"path","type":"address[]"}],"name":"getAmountsOut","outputs":[{"internalType":"uint256[]","name":"amounts","type":"uint256[]"}],"stateMutability":"view","type":"function"}
]""")

ERC20_ABI = json.loads("""[
  {"constant":false,"inputs":[{"name":"spender","type":"address"},{"name":"value","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},
  {"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},
  {"constant":true,"inputs":[{"name":"owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
  {"constant":true,"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"}
]""")

# ----------------------------------------------------------------------
# Global State
# ----------------------------------------------------------------------
active_trades: Dict[str, dict] = {}
recent: Dict[str, float] = {}
trade_lock = threading.RLock()   # FIX: use RLock to prevent deadlocks
scan_cycle_count = 0
exit_blacklist: Dict[str, float] = {}
web3_instances: Dict[str, Web3] = {}
wallet_account = None
solana_client: Optional[Any] = None
solana_keypair: Optional[Any] = None
SOL_MINT = "So11111111111111111111111111111111111111112"

# Momentum memory
momentum_history: Dict[str, list] = {}

# Performance telemetry
tp_hits = 0
partial_tp_hits = 0
sl_hits = 0
trailing_hits = 0
timeout_exits = 0
total_exits = 0
daily_pnl_usd = 0.0
daily_reset_time = time.time()
session_best_pct = 0.0
session_worst_pct = 0.0

halt_trading = False

# ----------------------------------------------------------------------
# Discord
# ----------------------------------------------------------------------
def send_discord_alert(content: str, embed: dict = None) -> bool:
    if not DISCORD_WEBHOOK_URL:
        return False
    payload = {"content": content}
    if embed:
        payload["embeds"] = [embed]
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        return resp.status_code == 204
    except Exception as e:
        logger.error(f"Discord webhook error: {e}")
        return False

# ----------------------------------------------------------------------
# Web3 / EVM
# ----------------------------------------------------------------------
def get_web3(chain: str) -> Optional[Web3]:
    if chain in web3_instances:
        return web3_instances[chain]
    rpc_url = RPC_URLS.get(chain)
    if not rpc_url:
        logger.error(f"No RPC URL for {chain}")
        return None
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
    if chain in ("bsc", "base"):
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    if not w3.is_connected():
        logger.error(f"Cannot connect to {chain} RPC")
        return None
    web3_instances[chain] = w3
    return w3

def get_wallet_account(w3: Web3):
    global wallet_account
    if wallet_account is None and WALLET_PRIVATE_KEY:
        wallet_account = w3.eth.account.from_key(WALLET_PRIVATE_KEY)
    return wallet_account

def execute_swap_evm(chain: str, token_in: str, token_out: str,
                     amount_in_wei: int, min_amount_out_wei: int, is_buy: bool) -> bool:
    if PAPER_MODE:
        return True
    w3 = get_web3(chain)
    if not w3 or not get_wallet_account(w3):
        return False
    router = w3.eth.contract(
        address=Web3.to_checksum_address(ROUTER_ADDRESSES[chain]), abi=ROUTER_ABI
    )
    path = [Web3.to_checksum_address(token_in), Web3.to_checksum_address(token_out)]
    deadline = int(time.time()) + 90
    try:
        if is_buy:
            tx = router.functions.swapExactETHForTokens(
                min_amount_out_wei, path, wallet_account.address, deadline
            ).build_transaction({
                "from": wallet_account.address,
                "value": amount_in_wei,
                "nonce": w3.eth.get_transaction_count(wallet_account.address),
                "gas": 350000,
                "gasPrice": int(w3.eth.gas_price * 1.1),  # +10% priority
            })
        else:
            token_contract = w3.eth.contract(
                address=Web3.to_checksum_address(token_in), abi=ERC20_ABI
            )
            allowance = token_contract.functions.allowance(
                wallet_account.address, ROUTER_ADDRESSES[chain]
            ).call()
            if allowance < amount_in_wei:
                approve_tx = token_contract.functions.approve(
                    ROUTER_ADDRESSES[chain], 2**256 - 1  # max approval
                ).build_transaction({
                    "from": wallet_account.address,
                    "nonce": w3.eth.get_transaction_count(wallet_account.address),
                    "gas": 100000,
                    "gasPrice": int(w3.eth.gas_price * 1.1),
                })
                signed_app = wallet_account.sign_transaction(approve_tx)
                w3.eth.send_raw_transaction(signed_app.rawTransaction)
                time.sleep(2)  # wait for approval
            tx = router.functions.swapExactTokensForETH(
                amount_in_wei, min_amount_out_wei, path, wallet_account.address, deadline
            ).build_transaction({
                "from": wallet_account.address,
                "nonce": w3.eth.get_transaction_count(wallet_account.address),
                "gas": 350000,
                "gasPrice": int(w3.eth.gas_price * 1.1),
            })
        signed = wallet_account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        return receipt.status == 1
    except Exception as e:
        logger.error(f"EVM swap error on {chain}: {e}")
        return False

# ----------------------------------------------------------------------
# Solana / Jupiter
# ----------------------------------------------------------------------
def init_solana():
    global solana_client, solana_keypair
    if not SOLANA_AVAILABLE or not SOLANA_PRIVATE_KEY:
        return False
    try:
        solana_client = SolanaClient(SOLANA_RPC_URL)
        secret_key = base58.b58decode(SOLANA_PRIVATE_KEY)
        solana_keypair = Keypair.from_bytes(secret_key)
        return True
    except Exception as e:
        logger.error(f"Solana init error: {e}")
        return False

def get_jupiter_quote(input_mint: str, output_mint: str, amount: int, slippage_bps: int = 100) -> Optional[dict]:
    try:
        params = {"inputMint": input_mint, "outputMint": output_mint,
                  "amount": amount, "slippageBps": slippage_bps}
        resp = requests.get(JUPITER_QUOTE_API, params=params, timeout=10)
        return resp.json() if resp.status_code == 200 else None
    except Exception as e:
        logger.error(f"Jupiter quote error: {e}")
        return None

def execute_jupiter_swap(quote_response: dict) -> Optional[str]:
    if not solana_keypair or not solana_client:
        return None
    try:
        swap_data = {
            "quoteResponse": quote_response,
            "userPublicKey": str(solana_keypair.pubkey()),
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": "auto",
        }
        resp = requests.post(JUPITER_SWAP_API, json=swap_data, timeout=15)
        if resp.status_code != 200:
            return None
        tx_data = resp.json()
        raw_tx = base58.b58decode(tx_data["swapTransaction"])
        tx = solana_client.send_raw_transaction(
            raw_tx, opts=TxOpts(skip_preflight=False, preflight_commitment=Confirmed)
        )
        return str(tx.value)
    except Exception as e:
        logger.error(f"Jupiter swap error: {e}")
        return None

def buy_token_solana(token_mint: str, amount_lamports: int) -> bool:
    if PAPER_MODE:
        return True
    quote = get_jupiter_quote(SOL_MINT, token_mint, amount_lamports, int(SWAP_SLIPPAGE_PCT * 100))
    return execute_jupiter_swap(quote) is not None if quote else False

def sell_token_solana(token_mint: str, amount_token_units: int) -> bool:
    if PAPER_MODE:
        return True
    quote = get_jupiter_quote(token_mint, SOL_MINT, amount_token_units, int(SWAP_SLIPPAGE_PCT * 100))
    return execute_jupiter_swap(quote) is not None if quote else False

def buy_token(chain: str, token_address: str, amount_usd: float) -> bool:
    if chain == "solana":
        try:
            url = f"https://api.dexscreener.com/latest/dex/search?q={SOL_MINT}"
            pairs = requests.get(url, timeout=10).json().get("pairs", [])
            sol_usd = float(next(
                (p["priceUsd"] for p in pairs if p.get("quoteToken", {}).get("symbol") in ("USDC", "USDT")), 100
            ))
            return buy_token_solana(token_address, int((amount_usd / sol_usd) * 1e9))
        except Exception as e:
            logger.error(f"Solana buy error: {e}")
            return False
    w3 = get_web3(chain)
    if not w3:
        return False
    try:
        weth = WETH_ADDRESSES[chain]
        url = f"https://api.dexscreener.com/latest/dex/search?q={weth}"
        pairs = requests.get(url, timeout=10).json().get("pairs", [])
        native_usd = float(next(
            (p["priceUsd"] for p in pairs if p.get("quoteToken", {}).get("symbol") in ("USDC", "USDT", "BUSD")), 2000
        ))
        amount_wei = w3.to_wei(amount_usd / native_usd, "ether")
        router = w3.eth.contract(address=Web3.to_checksum_address(ROUTER_ADDRESSES[chain]), abi=ROUTER_ABI)
        path = [weth, Web3.to_checksum_address(token_address)]
        amounts_out = router.functions.getAmountsOut(amount_wei, path).call()
        min_out = int(amounts_out[-1] * (1 - SWAP_SLIPPAGE_PCT / 100))
        return execute_swap_evm(chain, weth, token_address, amount_wei, min_out, is_buy=True)
    except Exception as e:
        logger.error(f"Buy error on {chain}: {e}")
        return False

def sell_token(chain: str, token_address: str, amount_units: int) -> bool:
    if chain == "solana":
        return sell_token_solana(token_address, amount_units)
    w3 = get_web3(chain)
    if not w3:
        return False
    try:
        weth = WETH_ADDRESSES[chain]
        router = w3.eth.contract(address=Web3.to_checksum_address(ROUTER_ADDRESSES[chain]), abi=ROUTER_ABI)
        path = [Web3.to_checksum_address(token_address), weth]
        amounts_out = router.functions.getAmountsOut(amount_units, path).call()
        min_out = int(amounts_out[-1] * (1 - SWAP_SLIPPAGE_PCT / 100))
        return execute_swap_evm(chain, token_address, weth, amount_units, min_out, is_buy=False)
    except Exception as e:
        logger.error(f"Sell error on {chain}: {e}")
        return False

# ----------------------------------------------------------------------
# DexScreener API
# ----------------------------------------------------------------------
def fetch_boosted_tokens(endpoint: str = "latest") -> List[dict]:
    url = f"https://api.dexscreener.com/token-boosts/{endpoint}/v1"
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 429:
            time.sleep(5)
            return []
        data = resp.json()
        if isinstance(data, list):
            return data
        elif isinstance(data, dict) and "url" in data:
            return [data]
        return []
    except Exception as e:
        logger.error(f"fetch_boosted_tokens error: {e}")
        return []

def fetch_pair_by_address(token_address: str) -> Optional[dict]:
    url = f"https://api.dexscreener.com/latest/dex/search?q={token_address}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 429:
            return None
        pairs = resp.json().get("pairs", [])
        return pairs[0] if pairs else None
    except Exception as e:
        logger.error(f"fetch_pair_by_address error: {e}")
        return None

def fetch_dex_pairs(query: str) -> List[dict]:
    url = f"https://api.de# =============================================================================
# DEX SCALPER PRO v4 - OPTIMIZED FOR 70%+ TAKE PROFIT RATE
# Strategy: Quality > Quantity. Fewer trades, higher conviction, smarter exits.
# =============================================================================

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
    _logging.getLogger("scalper").warning("Solana libraries not installed.")

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
# CONFIG — Tuned for 70%+ TP rate
# KEY PHILOSOPHY:
#   1. Only trade tokens that are ALREADY moving with confirmed momentum
#   2. Lock profit fast (partial TP at +2%), never let winners turn losers
#   3. Tight SL (-2%) but trailing stop preserves gains
#   4. Security check mandatory — no honeypots, ever
#   5. Age filter stops rug-pull micro-caps
# ----------------------------------------------------------------------

PAPER_MODE = os.getenv("PAPER_MODE", "true").lower() == "true"
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# Chain targets — BSC and Base have best liquidity/speed for scalping
TARGET_CHAINS = [c.strip().lower() for c in os.getenv("TARGET_CHAINS", "bsc,base").split(",") if c.strip()]

# === POSITION SIZING ===
MAX_TRADE_SIZE = float(os.getenv("MAX_TRADE_SIZE", "100.0"))
MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES", "3"))  # NEW: cap concurrent positions

# === EXIT STRATEGY — The core of 70% win rate ===
TAKE_PROFIT_PCT      = float(os.getenv("TAKE_PROFIT_PCT", "4.0"))       # Full TP target
PARTIAL_TP_PCT       = float(os.getenv("PARTIAL_TP_PCT", "2.0"))        # Sell 50% here fast
PARTIAL_TP_RATIO     = float(os.getenv("PARTIAL_TP_RATIO", "0.5"))      # How much to sell at partial TP
STOP_LOSS_PCT        = float(os.getenv("STOP_LOSS_PCT", "-2.0"))        # Hard SL — tight!
EMERGENCY_STOP_PCT   = float(os.getenv("EMERGENCY_STOP_PCT", "-4.0"))   # Absolute floor

# === TRAILING STOP — Locks profits after partial TP ===
TRAILING_STOP_ENABLED       = os.getenv("TRAILING_STOP_ENABLED", "true").lower() == "true"
TRAILING_ACTIVATION_PCT     = float(os.getenv("TRAILING_ACTIVATION_PCT", "2.0"))  # Activate after +2%
TRAILING_DISTANCE_PCT       = float(os.getenv("TRAILING_DISTANCE_PCT", "1.0"))    # Trail by 1%

# === TIME LIMIT ===
MAX_HOLD_MINUTES     = float(os.getenv("MAX_HOLD_MINUTES", "12.0"))     # Exit if no move in 12 min

# === ENTRY FILTERS — Strict quality gate ===
MIN_LIQUIDITY        = float(os.getenv("MIN_LIQUIDITY", "200000.0"))    # $200k min liq
MIN_VOLUME           = float(os.getenv("MIN_VOLUME", "50000.0"))        # $50k 24h volume
MIN_CHANGE           = float(os.getenv("MIN_CHANGE", "3.0"))            # m5 must be +3%+ 
MIN_M1_CHANGE        = float(os.getenv("MIN_M1_CHANGE", "0.5"))         # m1 positive required
MAX_M1_CHANGE        = float(os.getenv("MAX_M1_CHANGE", "8.0"))         # Avoid buying tops
MIN_AGE_HOURS        = float(os.getenv("MIN_AGE_HOURS", "1.0"))         # 1hr min age — avoid rugs
MAX_AGE_HOURS        = float(os.getenv("MAX_AGE_HOURS", "72.0"))        # 3 days max — need fresh momentum
MIN_PRICE_USD        = float(os.getenv("MIN_PRICE_USD", "0.000001"))
MAX_SELL_TAX         = float(os.getenv("MAX_SELL_TAX", "8.0"))          # Reject high tax tokens
MAX_BUY_TAX          = float(os.getenv("MAX_BUY_TAX", "8.0"))

# Volume acceleration: m5 vol must be > this multiple of average 5-min slice
VOL_ACCEL_MULTIPLIER = float(os.getenv("VOL_ACCEL_MULTIPLIER", "2.0"))  # 2x avg = real spike

# === TIMING ===
SCAN_INTERVAL_SECONDS   = int(os.getenv("SCAN_INTERVAL_SECONDS", "30"))  # Faster scans
FAST_MONITOR_INTERVAL   = int(os.getenv("FAST_MONITOR_INTERVAL", "3"))   # Tighter monitor

# === SCORE THRESHOLD ===
MIN_SCORE = float(os.getenv("MIN_SCORE", "75.0"))  # Slightly relaxed but better scoring

# === MOMENTUM CONFIRMATION ===
MOMENTUM_REQUIRED = int(os.getenv("MOMENTUM_REQUIRED", "2"))  # 2 consecutive positive m1 reads

# === RISK MANAGEMENT ===
BLACKLIST_AFTER_EXIT_HOURS  = float(os.getenv("BLACKLIST_AFTER_EXIT_HOURS", "6.0"))
MAX_DAILY_LOSS_USD          = float(os.getenv("MAX_DAILY_LOSS_USD", "200.0"))

# === SLIPPAGE ===
SLIPPAGE_PCT        = float(os.getenv("SLIPPAGE_PCT", "0.3"))
SWAP_SLIPPAGE_PCT   = float(os.getenv("SWAP_SLIPPAGE_PCT", "1.0"))

# === WALLET / RPC ===
WALLET_PRIVATE_KEY = os.getenv("WALLET_PRIVATE_KEY", "")
RPC_URLS = {
    "ethereum": os.getenv("RPC_URL_ETHEREUM", ""),
    "bsc":      os.getenv("RPC_URL_BSC", ""),
    "base":     os.getenv("RPC_URL_BASE", ""),
}
SOLANA_PRIVATE_KEY = os.getenv("SOLANA_PRIVATE_KEY", "")
SOLANA_RPC_URL     = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

JUPITER_QUOTE_API = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_API  = "https://quote-api.jup.ag/v6/swap"

CHAIN_ID_MAP = {
    "bsc": 56, "ethereum": 1, "polygon": 137, "avalanche": 43114,
    "fantom": 250, "arbitrum": 42161, "optimism": 10, "base": 8453, "solana": 101,
}
ROUTER_ADDRESSES = {
    "ethereum": "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
    "bsc":      "0x10ED43C718714eb63d5aA57B78B54704E256024E",
    "base":     "0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24",
}
WETH_ADDRESSES = {
    "ethereum": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
    "bsc":      "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
    "base":     "0x4200000000000000000000000000000000000006",
}

ROUTER_ABI = json.loads("""[
  {"inputs":[{"internalType":"uint256","name":"amountOutMin","type":"uint256"},{"internalType":"address[]","name":"path","type":"address[]"},{"internalType":"address","name":"to","type":"address"},{"internalType":"uint256","name":"deadline","type":"uint256"}],"name":"swapExactETHForTokens","outputs":[{"internalType":"uint256[]","name":"amounts","type":"uint256[]"}],"stateMutability":"payable","type":"function"},
  {"inputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"uint256","name":"amountOutMin","type":"uint256"},{"internalType":"address[]","name":"path","type":"address[]"},{"internalType":"address","name":"to","type":"address"},{"internalType":"uint256","name":"deadline","type":"uint256"}],"name":"swapExactTokensForETH","outputs":[{"internalType":"uint256[]","name":"amounts","type":"uint256[]"}],"stateMutability":"nonpayable","type":"function"},
  {"inputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"uint256","name":"amountOutMin","type":"uint256"},{"internalType":"address[]","name":"path","type":"address[]"},{"internalType":"address","name":"to","type":"address"},{"internalType":"uint256","name":"deadline","type":"uint256"}],"name":"swapExactTokensForTokens","outputs":[{"internalType":"uint256[]","name":"amounts","type":"uint256[]"}],"stateMutability":"nonpayable","type":"function"},
  {"inputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"address[]","name":"path","type":"address[]"}],"name":"getAmountsOut","outputs":[{"internalType":"uint256[]","name":"amounts","type":"uint256[]"}],"stateMutability":"view","type":"function"}
]""")

ERC20_ABI = json.loads("""[
  {"constant":false,"inputs":[{"name":"spender","type":"address"},{"name":"value","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},
  {"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},
  {"constant":true,"inputs":[{"name":"owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
  {"constant":true,"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"}
]""")

# ----------------------------------------------------------------------
# Global State
# ----------------------------------------------------------------------
active_trades: Dict[str, dict] = {}
recent: Dict[str, float] = {}
trade_lock = threading.RLock()   # FIX: use RLock to prevent deadlocks
scan_cycle_count = 0
exit_blacklist: Dict[str, float] = {}
web3_instances: Dict[str, Web3] = {}
wallet_account = None
solana_client: Optional[Any] = None
solana_keypair: Optional[Any] = None
SOL_MINT = "So11111111111111111111111111111111111111112"

# Momentum memory
momentum_history: Dict[str, list] = {}

# Performance telemetry
tp_hits = 0
partial_tp_hits = 0
sl_hits = 0
trailing_hits = 0
timeout_exits = 0
total_exits = 0
daily_pnl_usd = 0.0
daily_reset_time = time.time()
session_best_pct = 0.0
session_worst_pct = 0.0

halt_trading = False

# ----------------------------------------------------------------------
# Discord
# ----------------------------------------------------------------------
def send_discord_alert(content: str, embed: dict = None) -> bool:
    if not DISCORD_WEBHOOK_URL:
        return False
    payload = {"content": content}
    if embed:
        payload["embeds"] = [embed]
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        return resp.status_code == 204
    except Exception as e:
        logger.error(f"Discord webhook error: {e}")
        return False

# ----------------------------------------------------------------------
# Web3 / EVM
# ----------------------------------------------------------------------
def get_web3(chain: str) -> Optional[Web3]:
    if chain in web3_instances:
        return web3_instances[chain]
    rpc_url = RPC_URLS.get(chain)
    if not rpc_url:
        logger.error(f"No RPC URL for {chain}")
        return None
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
    if chain in ("bsc", "base"):
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    if not w3.is_connected():
        logger.error(f"Cannot connect to {chain} RPC")
        return None
    web3_instances[chain] = w3
    return w3

def get_wallet_account(w3: Web3):
    global wallet_account
    if wallet_account is None and WALLET_PRIVATE_KEY:
        wallet_account = w3.eth.account.from_key(WALLET_PRIVATE_KEY)
    return wallet_account

def execute_swap_evm(chain: str, token_in: str, token_out: str,
                     amount_in_wei: int, min_amount_out_wei: int, is_buy: bool) -> bool:
    if PAPER_MODE:
        return True
    w3 = get_web3(chain)
    if not w3 or not get_wallet_account(w3):
        return False
    router = w3.eth.contract(
        address=Web3.to_checksum_address(ROUTER_ADDRESSES[chain]), abi=ROUTER_ABI
    )
    path = [Web3.to_checksum_address(token_in), Web3.to_checksum_address(token_out)]
    deadline = int(time.time()) + 90
    try:
        if is_buy:
            tx = router.functions.swapExactETHForTokens(
                min_amount_out_wei, path, wallet_account.address, deadline
            ).build_transaction({
                "from": wallet_account.address,
                "value": amount_in_wei,
                "nonce": w3.eth.get_transaction_count(wallet_account.address),
                "gas": 350000,
                "gasPrice": int(w3.eth.gas_price * 1.1),  # +10% priority
            })
        else:
            token_contract = w3.eth.contract(
                address=Web3.to_checksum_address(token_in), abi=ERC20_ABI
            )
            allowance = token_contract.functions.allowance(
                wallet_account.address, ROUTER_ADDRESSES[chain]
            ).call()
            if allowance < amount_in_wei:
                approve_tx = token_contract.functions.approve(
                    ROUTER_ADDRESSES[chain], 2**256 - 1  # max approval
                ).build_transaction({
                    "from": wallet_account.address,
                    "nonce": w3.eth.get_transaction_count(wallet_account.address),
                    "gas": 100000,
                    "gasPrice": int(w3.eth.gas_price * 1.1),
                })
                signed_app = wallet_account.sign_transaction(approve_tx)
                w3.eth.send_raw_transaction(signed_app.rawTransaction)
                time.sleep(2)  # wait for approval
            tx = router.functions.swapExactTokensForETH(
                amount_in_wei, min_amount_out_wei, path, wallet_account.address, deadline
            ).build_transaction({
                "from": wallet_account.address,
                "nonce": w3.eth.get_transaction_count(wallet_account.address),
                "gas": 350000,
                "gasPrice": int(w3.eth.gas_price * 1.1),
            })
        signed = wallet_account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        return receipt.status == 1
    except Exception as e:
        logger.error(f"EVM swap error on {chain}: {e}")
        return False

# ----------------------------------------------------------------------
# Solana / Jupiter
# ----------------------------------------------------------------------
def init_solana():
    global solana_client, solana_keypair
    if not SOLANA_AVAILABLE or not SOLANA_PRIVATE_KEY:
        return False
    try:
        solana_client = SolanaClient(SOLANA_RPC_URL)
        secret_key = base58.b58decode(SOLANA_PRIVATE_KEY)
        solana_keypair = Keypair.from_bytes(secret_key)
        return True
    except Exception as e:
        logger.error(f"Solana init error: {e}")
        return False

def get_jupiter_quote(input_mint: str, output_mint: str, amount: int, slippage_bps: int = 100) -> Optional[dict]:
    try:
        params = {"inputMint": input_mint, "outputMint": output_mint,
                  "amount": amount, "slippageBps": slippage_bps}
        resp = requests.get(JUPITER_QUOTE_API, params=params, timeout=10)
        return resp.json() if resp.status_code == 200 else None
    except Exception as e:
        logger.error(f"Jupiter quote error: {e}")
        return None

def execute_jupiter_swap(quote_response: dict) -> Optional[str]:
    if not solana_keypair or not solana_client:
        return None
    try:
        swap_data = {
            "quoteResponse": quote_response,
            "userPublicKey": str(solana_keypair.pubkey()),
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": "auto",
        }
        resp = requests.post(JUPITER_SWAP_API, json=swap_data, timeout=15)
        if resp.status_code != 200:
            return None
        tx_data = resp.json()
        raw_tx = base58.b58decode(tx_data["swapTransaction"])
        tx = solana_client.send_raw_transaction(
            raw_tx, opts=TxOpts(skip_preflight=False, preflight_commitment=Confirmed)
        )
        return str(tx.value)
    except Exception as e:
        logger.error(f"Jupiter swap error: {e}")
        return None

def buy_token_solana(token_mint: str, amount_lamports: int) -> bool:
    if PAPER_MODE:
        return True
    quote = get_jupiter_quote(SOL_MINT, token_mint, amount_lamports, int(SWAP_SLIPPAGE_PCT * 100))
    return execute_jupiter_swap(quote) is not None if quote else False

def sell_token_solana(token_mint: str, amount_token_units: int) -> bool:
    if PAPER_MODE:
        return True
    quote = get_jupiter_quote(token_mint, SOL_MINT, amount_token_units, int(SWAP_SLIPPAGE_PCT * 100))
    return execute_jupiter_swap(quote) is not None if quote else False

def buy_token(chain: str, token_address: str, amount_usd: float) -> bool:
    if chain == "solana":
        try:
            url = f"https://api.dexscreener.com/latest/dex/search?q={SOL_MINT}"
            pairs = requests.get(url, timeout=10).json().get("pairs", [])
            sol_usd = float(next(
                (p["priceUsd"] for p in pairs if p.get("quoteToken", {}).get("symbol") in ("USDC", "USDT")), 100
            ))
            return buy_token_solana(token_address, int((amount_usd / sol_usd) * 1e9))
        except Exception as e:
            logger.error(f"Solana buy error: {e}")
            return False
    w3 = get_web3(chain)
    if not w3:
        return False
    try:
        weth = WETH_ADDRESSES[chain]
        url = f"https://api.dexscreener.com/latest/dex/search?q={weth}"
        pairs = requests.get(url, timeout=10).json().get("pairs", [])
        native_usd = float(next(
            (p["priceUsd"] for p in pairs if p.get("quoteToken", {}).get("symbol") in ("USDC", "USDT", "BUSD")), 2000
        ))
        amount_wei = w3.to_wei(amount_usd / native_usd, "ether")
        router = w3.eth.contract(address=Web3.to_checksum_address(ROUTER_ADDRESSES[chain]), abi=ROUTER_ABI)
        path = [weth, Web3.to_checksum_address(token_address)]
        amounts_out = router.functions.getAmountsOut(amount_wei, path).call()
        min_out = int(amounts_out[-1] * (1 - SWAP_SLIPPAGE_PCT / 100))
        return execute_swap_evm(chain, weth, token_address, amount_wei, min_out, is_buy=True)
    except Exception as e:
        logger.error(f"Buy error on {chain}: {e}")
        return False

def sell_token(chain: str, token_address: str, amount_units: int) -> bool:
    if chain == "solana":
        return sell_token_solana(token_address, amount_units)
    w3 = get_web3(chain)
    if not w3:
        return False
    try:
        weth = WETH_ADDRESSES[chain]
        router = w3.eth.contract(address=Web3.to_checksum_address(ROUTER_ADDRESSES[chain]), abi=ROUTER_ABI)
        path = [Web3.to_checksum_address(token_address), weth]
        amounts_out = router.functions.getAmountsOut(amount_units, path).call()
        min_out = int(amounts_out[-1] * (1 - SWAP_SLIPPAGE_PCT / 100))
        return execute_swap_evm(chain, token_address, weth, amount_units, min_out, is_buy=False)
    except Exception as e:
        logger.error(f"Sell error on {chain}: {e}")
        return False

# ----------------------------------------------------------------------
# DexScreener API
# ----------------------------------------------------------------------
def fetch_boosted_tokens(endpoint: str = "latest") -> List[dict]:
    url = f"https://api.dexscreener.com/token-boosts/{endpoint}/v1"
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 429:
            time.sleep(5)
            return []
        data = resp.json()
        if isinstance(data, list):
            return data
        elif isinstance(data, dict) and "url" in data:
            return [data]
        return []
    except Exception as e:
        logger.error(f"fetch_boosted_tokens error: {e}")
        return []

def fetch_pair_by_address(token_address: str) -> Optional[dict]:
    url = f"https://api.dexscreener.com/latest/dex/search?q={token_address}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 429:
            return None
        pairs = resp.json().get("pairs", [])
        return pairs[0] if pairs else None
    except Exception as e:
        logger.error(f"fetch_pair_by_address error: {e}")
        return None

def fetch_dex_pairs(query: str) -> List[dict]:
    url = f"https://api.de
