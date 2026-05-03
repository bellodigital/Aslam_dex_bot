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
    _logging.getLogger("scalper").warning("Solana libs not installed; Solana trading disabled.")

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
# Configuration (relaxed defaults for immediate trade flow)
# ----------------------------------------------------------------------
PAPER_MODE = os.getenv("PAPER_MODE", "true").lower() == "true"
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

TARGET_CHAINS = [c.strip().lower() for c in os.getenv("TARGET_CHAINS", "bsc,base,solana,ethereum").split(",") if c.strip()]

MAX_TRADE_SIZE = float(os.getenv("MAX_TRADE_SIZE", "100.0"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "-2.5"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "3.0"))
PARTIAL_TP_PCT = float(os.getenv("PARTIAL_TP_PCT", "1.5"))  # 50% sell target
TRAILING_STOP_ENABLED = os.getenv("TRAILING_STOP_ENABLED", "true").lower() == "true"
TRAILING_ACTIVATION_PCT = float(os.getenv("TRAILING_ACTIVATION_PCT", "1.5"))
TRAILING_DISTANCE_PCT = float(os.getenv("TRAILING_DISTANCE_PCT", "0.8"))
MAX_HOLD_MINUTES = float(os.getenv("MAX_HOLD_MINUTES", "8.0"))
PULLBACK_ENTRY_PCT = float(os.getenv("PULLBACK_ENTRY_PCT", "0"))   # disabled for now
SLIPPAGE_PCT = float(os.getenv("SLIPPAGE_PCT", "0.3"))
SWAP_SLIPPAGE_PCT = float(os.getenv("SWAP_SLIPPAGE_PCT", "0.8"))

# Relaxed yet still strong filters
MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "150000.0"))
MIN_VOLUME = float(os.getenv("MIN_VOLUME", "30000.0"))
MIN_CHANGE = float(os.getenv("MIN_CHANGE", "2.0"))
MIN_AGE_HOURS = float(os.getenv("MIN_AGE_HOURS", "0.5"))
MIN_PRICE_USD = float(os.getenv("MIN_PRICE_USD", "0.0001"))
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "60"))
FAST_MONITOR_INTERVAL = int(os.getenv("FAST_MONITOR_INTERVAL", "5"))
MIN_SCORE = float(os.getenv("MIN_SCORE", "80.0"))

BLACKLIST_AFTER_EXIT_HOURS = float(os.getenv("BLACKLIST_AFTER_EXIT_HOURS", "4.0"))

# Daily loss limit
MAX_DAILY_LOSS_USD = float(os.getenv("MAX_DAILY_LOSS_USD", "200.0"))

# Wallet & RPC
WALLET_PRIVATE_KEY = os.getenv("WALLET_PRIVATE_KEY", "")
RPC_URLS = {
    "ethereum": os.getenv("RPC_URL_ETHEREUM", ""),
    "bsc": os.getenv("RPC_URL_BSC", ""),
    "base": os.getenv("RPC_URL_BASE", ""),
}
SOLANA_PRIVATE_KEY = os.getenv("SOLANA_PRIVATE_KEY", "")
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

JUPITER_QUOTE_API = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_API = "https://quote-api.jup.ag/v6/swap"

CHAIN_ID_MAP = {
    "bsc": 56, "ethereum": 1, "polygon": 137, "avalanche": 43114,
    "fantom": 250, "arbitrum": 42161, "optimism": 10, "base": 8453,
    "solana": 101,
}

ROUTER_ADDRESSES = {
    "ethereum": "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
    "bsc": "0x10ED43C718714eb63d5aA57B78B54704E256024E",
    "base": "0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24",
}

WETH_ADDRESSES = {
    "ethereum": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
    "bsc": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
    "base": "0x4200000000000000000000000000000000000006",
}

ROUTER_ABI = json.loads("""[
    {
        "inputs": [
            {"internalType":"uint256","name":"amountOutMin","type":"uint256"},
            {"internalType":"address[]","name":"path","type":"address[]"},
            {"internalType":"address","name":"to","type":"address"},
            {"internalType":"uint256","name":"deadline","type":"uint256"}
        ],
        "name":"swapExactETHForTokens",
        "outputs":[{"internalType":"uint256[]","name":"amounts","type":"uint256[]"}],
        "stateMutability":"payable","type":"function"
    },
    {
        "inputs":[
            {"internalType":"uint256","name":"amountIn","type":"uint256"},
            {"internalType":"uint256","name":"amountOutMin","type":"uint256"},
            {"internalType":"address[]","name":"path","type":"address[]"},
            {"internalType":"address","name":"to","type":"address"},
            {"internalType":"uint256","name":"deadline","type":"uint256"}
        ],
        "name":"swapExactTokensForETH",
        "outputs":[{"internalType":"uint256[]","name":"amounts","type":"uint256[]"}],
        "stateMutability":"nonpayable","type":"function"
    },
    {
        "inputs":[
            {"internalType":"uint256","name":"amountIn","type":"uint256"},
            {"internalType":"uint256","name":"amountOutMin","type":"uint256"},
            {"internalType":"address[]","name":"path","type":"address[]"},
            {"internalType":"address","name":"to","type":"address"},
            {"internalType":"uint256","name":"deadline","type":"uint256"}
        ],
        "name":"swapExactTokensForTokens",
        "outputs":[{"internalType":"uint256[]","name":"amounts","type":"uint256[]"}],
        "stateMutability":"nonpayable","type":"function"
    },
    {
        "inputs":[
            {"internalType":"uint256","name":"amountIn","type":"uint256"},
            {"internalType":"address[]","name":"path","type":"address[]"}
        ],
        "name":"getAmountsOut",
        "outputs":[{"internalType":"uint256[]","name":"amounts","type":"uint256[]"}],
        "stateMutability":"view","type":"function"
    }
]""")

ERC20_ABI = json.loads("""[
    {"constant":false,"inputs":[{"name":"spender","type":"address"},{"name":"value","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},
    {"constant":true,"inputs":[{"name":"owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"}
]""")

# ----------------------------------------------------------------------
# Global State
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

# Momentum confirmation memory
momentum_history: Dict[str, list] = {}   # token_addr -> [(timestamp, m1), ...]

# Performance telemetry
tp_hits = 0
sl_hits = 0
total_exits = 0
daily_pnl_usd = 0.0
daily_reset_time = time.time()

# Emergency halt flag
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
# Web3 / Swap (EVM)
# ----------------------------------------------------------------------
def get_web3(chain: str) -> Optional[Web3]:
    if chain in web3_instances:
        return web3_instances[chain]
    rpc_url = RPC_URLS.get(chain)
    if not rpc_url:
        return None
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if chain in ("bsc", "base"):
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    if not w3.is_connected():
        return None
    web3_instances[chain] = w3
    return w3

def get_wallet_account(w3: Web3):
    global wallet_account
    if wallet_account is None and WALLET_PRIVATE_KEY:
        wallet_account = w3.eth.account.from_key(WALLET_PRIVATE_KEY)
    return wallet_account

def execute_swap_evm(chain: str, token_in: str, token_out: str, amount_in_wei: int, min_amount_out_wei: int, is_buy: bool) -> bool:
    if PAPER_MODE:
        return True
    w3 = get_web3(chain)
    if not w3 or not get_wallet_account(w3):
        return False
    router = w3.eth.contract(address=Web3.to_checksum_address(ROUTER_ADDRESSES[chain]), abi=ROUTER_ABI)
    path = [Web3.to_checksum_address(token_in), Web3.to_checksum_address(token_out)]
    deadline = int(time.time()) + 60
    try:
        if is_buy:
            tx = router.functions.swapExactETHForTokens(
                min_amount_out_wei, path, wallet_account.address, deadline
            ).build_transaction({
                'from': wallet_account.address,
                'value': amount_in_wei,
                'nonce': w3.eth.get_transaction_count(wallet_account.address),
                'gas': 300000,
                'gasPrice': w3.eth.gas_price
            })
        else:
            token_contract = w3.eth.contract(address=Web3.to_checksum_address(token_in), abi=ERC20_ABI)
            allowance = token_contract.functions.allowance(wallet_account.address, ROUTER_ADDRESSES[chain]).call()
            if allowance < amount_in_wei:
                approve_tx = token_contract.functions.approve(ROUTER_ADDRESSES[chain], amount_in_wei).build_transaction({
                    'from': wallet_account.address,
                    'nonce': w3.eth.get_transaction_count(wallet_account.address),
                    'gas': 100000,
                    'gasPrice': w3.eth.gas_price
                })
                signed_app = wallet_account.sign_transaction(approve_tx)
                w3.eth.send_raw_transaction(signed_app.rawTransaction)
            tx = router.functions.swapExactTokensForETH(
                amount_in_wei, min_amount_out_wei, path, wallet_account.address, deadline
            ).build_transaction({
                'from': wallet_account.address,
                'nonce': w3.eth.get_transaction_count(wallet_account.address),
                'gas': 300000,
                'gasPrice': w3.eth.gas_price
            })
        signed = wallet_account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        return receipt.status == 1
    except Exception as e:
        logger.error(f"EVM swap error on {chain}: {e}")
        return False

# ... (Solana helpers, same as before) ...

def init_solana():
    global solana_client, solana_keypair
    if not SOLANA_AVAILABLE or not SOLANA_PRIVATE_KEY:
        return False
    try:
        solana_client = SolanaClient(SOLANA_RPC_URL)
        secret_key = base58.b58decode(SOLANA_PRIVATE_KEY)
        solana_keypair = Keypair.from_bytes(secret_key)
        return True
    except:
        return False

def get_jupiter_quote(input_mint: str, output_mint: str, amount: int, slippage_bps: int = 100) -> Optional[dict]:
    params = {"inputMint": input_mint, "outputMint": output_mint, "amount": amount, "slippageBps": slippage_bps}
    resp = requests.get(JUPITER_QUOTE_API, params=params)
    return resp.json() if resp.status_code == 200 else None

def execute_jupiter_swap(quote_response: dict) -> Optional[str]:
    if not solana_keypair or not solana_client:
        return None
    swap_data = {
        "quoteResponse": quote_response,
        "userPublicKey": str(solana_keypair.pubkey()),
        "wrapAndUnwrapSol": True,
        "dynamicComputeUnitLimit": True,
        "prioritizationFeeLamports": "auto"
    }
    resp = requests.post(JUPITER_SWAP_API, json=swap_data)
    if resp.status_code != 200:
        return None
    tx_data = resp.json()
    raw_tx = base58.b58decode(tx_data["swapTransaction"])
    tx = solana_client.send_raw_transaction(raw_tx, opts=TxOpts(skip_preflight=False, preflight_commitment=Confirmed))
    return str(tx.value)

def buy_token_solana(token_mint: str, amount_lamports: int) -> bool:
    if PAPER_MODE:
        return True
    quote = get_jupiter_quote(SOL_MINT, token_mint, amount_lamports, int(SWAP_SLIPPAGE_PCT * 100))
    if not quote:
        return False
    sig = execute_jupiter_swap(quote)
    return sig is not None

def sell_token_solana(token_mint: str, amount_token_units: int) -> bool:
    if PAPER_MODE:
        return True
    quote = get_jupiter_quote(token_mint, SOL_MINT, amount_token_units, int(SWAP_SLIPPAGE_PCT * 100))
    if not quote:
        return False
    sig = execute_jupiter_swap(quote)
    return sig is not None

def buy_token(chain: str, token_address: str, amount_usd: float) -> bool:
    if chain == "solana":
        try:
            url = f"https://api.dexscreener.com/latest/dex/search?q={SOL_MINT}"
            pairs = requests.get(url, timeout=10).json().get("pairs", [])
            sol_usd = float(next((p["priceUsd"] for p in pairs if p.get("quoteToken",{}).get("symbol") in ("USDC","USDT")), 100))
            amount_lamports = int((amount_usd / sol_usd) * 1e9)
            return buy_token_solana(token_address, amount_lamports)
        except:
            return False
    w3 = get_web3(chain)
    if not w3:
        return False
    try:
        weth = WETH_ADDRESSES[chain]
        url = f"https://api.dexscreener.com/latest/dex/search?q={weth}"
        pairs = requests.get(url, timeout=10).json().get("pairs", [])
        native_usd = float(next((p["priceUsd"] for p in pairs if p.get("quoteToken",{}).get("symbol") in ("USDC","USDT","BUSD")), 2000))
        amount_wei = w3.to_wei(amount_usd / native_usd, 'ether')
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
# API functions (unchanged)
# ----------------------------------------------------------------------
def fetch_boosted_tokens(endpoint: str = "latest") -> List[dict]:
    url = f"https://api.dexscreener.com/token-boosts/{endpoint}/v1"
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 429:
            return []
        data = resp.json()
        if isinstance(data, list):
            return data
        elif isinstance(data, dict) and "url" in data:
            return [data]
        return []
    except:
        return []

def fetch_pair_by_address(token_address: str) -> Optional[dict]:
    url = f"https://api.dexscreener.com/latest/dex/search?q={token_address}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 429:
            return None
        pairs = resp.json().get("pairs", [])
        return pairs[0] if pairs else None
    except:
        return None

def fetch_dex_pairs(query: str) -> List[dict]:
    url = f"https://api.dexscreener.com/latest/dex/search?q={query}"
    try:
        resp = requests.get(url, timeout=15)
        return resp.json().get("pairs", [])
    except:
        return []

def fetch_pair_price(chain: str, pair_address: str) -> Optional[float]:
    url = f"https://api.dexscreener.com/latest/dex/pairs/{chain}/{pair_address}"
    try:
        resp = requests.get(url, timeout=5)
        data = resp.json().get("pair", {})
        return float(data.get("priceUsd", 0))
    except:
        return None

# ----------------------------------------------------------------------
# Improved Filters with volume acceleration
# ----------------------------------------------------------------------
def filter_pairs(pairs: List[dict]) -> List[dict]:
    now_ms = int(time.time() * 1000)
    valid = []
    for pair in pairs:
        try:
            chain = pair.get("chainId")
            if chain not in TARGET_CHAINS:
                continue
            price = float(pair.get("priceUsd", 0))
            if price < MIN_PRICE_USD:
                continue
            liq = float(pair.get("liquidity", {}).get("usd", 0))
            vol = float(pair.get("volume", {}).get("h24", 0))
            m5  = float(pair.get("priceChange", {}).get("m5", 0))
            m1  = float(pair.get("priceChange", {}).get("m1", 0))
            h1  = float(pair.get("priceChange", {}).get("h1", 0))
            created = int(pair.get("pairCreatedAt", 0))
            age_hours = (now_ms - created) / 3600000 if created else 0

            if liq < MIN_LIQUIDITY or vol < MIN_VOLUME or m5 < MIN_CHANGE:
                continue
            if MIN_AGE_HOURS > 0 and age_hours < MIN_AGE_HOURS:
                continue
            if m1 <= 0:
                continue

            # Volume acceleration: m5 volume should be > 1.5x average 5-min slice of h1 vol
            vol_m5 = float(pair.get("volume", {}).get("m5", 0))
            vol_h1 = float(pair.get("volume", {}).get("h1", 0))
            if vol_h1 > 0 and vol_m5 < (vol_h1 / 12) * 1.5:
                continue   # no volume spike

            pair["_liq"] = liq
            pair["_vol"] = vol
            pair["_m5"]  = m5
            pair["_m1"]  = m1
            pair["_h1"]  = h1
            pair["_age_hours"] = age_hours
            pair["_price"] = price
            pair["_chain"] = chain
            valid.append(pair)
        except:
            continue
    logger.info(f"Filter summary: {len(pairs)} input, {len(valid)} passed for {TARGET_CHAINS}")
    return valid

def is_pullback_entry(pair: dict) -> bool:
    if PULLBACK_ENTRY_PCT <= 0:
        return True
    try:
        m5 = pair.get("_m5", 0)
        m1 = pair.get("_m1", 0)
        if m5 <= 0:
            return False
        if m1 <= 0.2 or m1 > 4.0:
            return False
        if m5 < m1 * 1.5:
            return False
        return True
    except:
        return True

# ----------------------------------------------------------------------
# Momentum Confirmation (reduced to 1 reading)
# ----------------------------------------------------------------------
def confirm_momentum(pair: dict, required: int = 1) -> bool:
    token_addr = pair.get(
