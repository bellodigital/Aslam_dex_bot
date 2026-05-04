I'm# =============================================================================
# DEX SCALPER PRO v4 - OPTIMIZED FOR 70%+ TAKE PROFIT RATE
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scalper")

#----------------- config -----------------
PAPER_MODE = os.getenv("PAPER_MODE", "true").lower() == "true"
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
TARGET_CHAINS = [c.strip().lower() for c in os.getenv("TARGET_CHAINS", "bsc,base").split(",") if c.strip()]

MAX_TRADE_SIZE = float(os.getenv("MAX_TRADE_SIZE", "100.0"))
MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES", "3"))

TAKE_PROFIT_PCT      = float(os.getenv("TAKE_PROFIT_PCT", "4.0"))
PARTIAL_TP_PCT       = float(os.getenv("PARTIAL_TP_PCT", "2.0"))
PARTIAL_TP_RATIO     = float(os.getenv("PARTIAL_TP_RATIO", "0.5"))
STOP_LOSS_PCT        = float(os.getenv("STOP_LOSS_PCT", "-2.0"))
EMERGENCY_STOP_PCT   = float(os.getenv("EMERGENCY_STOP_PCT", "-4.0"))
TRAILING_STOP_ENABLED   = os.getenv("TRAILING_STOP_ENABLED", "true").lower() == "true"
TRAILING_ACTIVATION_PCT = float(os.getenv("TRAILING_ACTIVATION_PCT", "2.0"))
TRAILING_DISTANCE_PCT   = float(os.getenv("TRAILING_DISTANCE_PCT", "1.0"))

MAX_HOLD_MINUTES     = float(os.getenv("MAX_HOLD_MINUTES", "12.0"))

MIN_LIQUIDITY        = float(os.getenv("MIN_LIQUIDITY", "200000.0"))
MIN_VOLUME           = float(os.getenv("MIN_VOLUME", "50000.0"))
MIN_CHANGE           = float(os.getenv("MIN_CHANGE", "3.0"))
MIN_M1_CHANGE        = float(os.getenv("MIN_M1_CHANGE", "0.5"))
MAX_M1_CHANGE        = float(os.getenv("MAX_M1_CHANGE", "8.0"))
MIN_AGE_HOURS        = float(os.getenv("MIN_AGE_HOURS", "1.0"))
MAX_AGE_HOURS        = float(os.getenv("MAX_AGE_HOURS", "72.0"))
MIN_PRICE_USD        = float(os.getenv("MIN_PRICE_USD", "0.000001"))
MAX_SELL_TAX         = float(os.getenv("MAX_SELL_TAX", "8.0"))
MAX_BUY_TAX          = float(os.getenv("MAX_BUY_TAX", "8.0"))
VOL_ACCEL_MULTIPLIER = float(os.getenv("VOL_ACCEL_MULTIPLIER", "2.0"))

SCAN_INTERVAL_SECONDS  = int(os.getenv("SCAN_INTERVAL_SECONDS", "30"))
FAST_MONITOR_INTERVAL  = int(os.getenv("FAST_MONITOR_INTERVAL", "3"))
MIN_SCORE              = float(os.getenv("MIN_SCORE", "75.0"))
MOMENTUM_REQUIRED      = int(os.getenv("MOMENTUM_REQUIRED", "2"))

BLACKLIST_AFTER_EXIT_HOURS = float(os.getenv("BLACKLIST_AFTER_EXIT_HOURS", "6.0"))
MAX_DAILY_LOSS_USD         = float(os.getenv("MAX_DAILY_LOSS_USD", "200.0"))

SLIPPAGE_PCT      = float(os.getenv("SLIPPAGE_PCT", "0.3"))
SWAP_SLIPPAGE_PCT = float(os.getenv("SWAP_SLIPPAGE_PCT", "1.0"))

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

SOL_MINT = "So11111111111111111111111111111111111111112"

#-------------- global state --------------
active_trades: Dict[str, dict] = {}
recent: Dict[str, float] = {}
trade_lock = threading.RLock()
scan_cycle_count = 0
exit_blacklist: Dict[str, float] = {}
web3_instances: Dict[str, Web3] = {}
wallet_account = None
solana_client = None
solana_keypair = None
momentum_history: Dict[str, list] = {}

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
# -- DISCORD --
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
        logger.error(f"Discord error: {e}")
        return False

# -- WEB3 / EVM --
def get_web3(chain: str):
    if chain in web3_instances:
        return web3_instances[chain]
    rpc_url = RPC_URLS.get(chain)
    if not rpc_url:
        return None
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
    if chain in ("bsc", "base"):
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    if not w3.is_connected():
        return None
    web3_instances[chain] = w3
    return w3

def get_wallet_account(w3):
    global wallet_account
    if wallet_account is None and WALLET_PRIVATE_KEY:
        wallet_account = w3.eth.account.from_key(WALLET_PRIVATE_KEY)
    return wallet_account

def execute_swap_evm(chain, token_in, token_out, amount_in_wei, min_amount_out_wei, is_buy):
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
                "from": wallet_account.address, "value": amount_in_wei,
                "nonce": w3.eth.get_transaction_count(wallet_account.address),
                "gas": 350000, "gasPrice": int(w3.eth.gas_price * 1.1),
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
                    ROUTER_ADDRESSES[chain], 2**256 - 1
                ).build_transaction({
                    "from": wallet_account.address,
                    "nonce": w3.eth.get_transaction_count(wallet_account.address),
                    "gas": 100000, "gasPrice": int(w3.eth.gas_price * 1.1),
                })
                signed_app = wallet_account.sign_transaction(approve_tx)
                w3.eth.send_raw_transaction(signed_app.rawTransaction)
                time.sleep(2)
            tx = router.functions.swapExactTokensForETH(
                amount_in_wei, min_amount_out_wei, path, wallet_account.address, deadline
            ).build_transaction({
                "from": wallet_account.address,
                "nonce": w3.eth.get_transaction_count(wallet_account.address),
                "gas": 350000, "gasPrice": int(w3.eth.gas_price * 1.1),
            })
        signed = wallet_account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        return receipt.status == 1
    except Exception as e:
        logger.error(f"EVM swap error: {e}")
        return False

# -- SOLANA / JUPITER --
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

def get_jupiter_quote(input_mint, output_mint, amount, slippage_bps=100):
    try:
        params = {"inputMint": input_mint, "outputMint": output_mint,
                  "amount": amount, "slippageBps": slippage_bps}
        resp = requests.get(JUPITER_QUOTE_API, params=params, timeout=10)
        return resp.json() if resp.status_code == 200 else None
    except Exception as e:
        logger.error(f"Jupiter quote error: {e}")
        return None

def execute_jupiter_swap(quote_response):
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

def buy_token(chain, token_address, amount_usd):
    if chain == "solana":
        try:
            url = f"https://api.dexscreener.com/latest/dex/search?q={SOL_MINT}"
            pairs = requests.get(url, timeout=10).json().get("pairs", [])
            sol_usd = float(next(
                (p["priceUsd"] for p in pairs if p.get("quoteToken", {}).get("symbol") in ("USDC","USDT")), 100
            ))
            return execute_jupiter_swap(
                get_jupiter_quote(SOL_MINT, token_address, int((amount_usd / sol_usd) * 1e9))
            ) is not None
        except Exception as e:
            logger.error(f"Solana buy error: {e}")
            return False
    w3 = get_web3(chain)
    if not w3:
        return False
    try:
        weth = WETH_ADDRESSES[chain]
        pairs = requests.get(
            f"https://api.dexscreener.com/latest/dex/search?q={weth}", timeout=10
        ).json().get("pairs", [])
        native_usd = float(next(
            (p["priceUsd"] for p in pairs if p.get("quoteToken", {}).get("symbol") in ("USDC","USDT","BUSD")), 2000
        ))
        amount_wei = w3.to_wei(amount_usd / native_usd, "ether")
        router = w3.eth.contract(address=Web3.to_checksum_address(ROUTER_ADDRESSES[chain]), abi=ROUTER_ABI)
        path = [weth, Web3.to_checksum_address(token_address)]
        amounts_out = router.functions.getAmountsOut(amount_wei, path).call()
        min_out = int(amounts_out[-1] * (1 - SWAP_SLIPPAGE_PCT / 100))
        return execute_swap_evm(chain, weth, token_address, amount_wei, min_out, is_buy=True)
    except Exception as e:
        logger.error(f"Buy error: {e}")
        return False

def sell_token(chain, token_address, amount_units):
    if chain == "solana":
        quote = get_jupiter_quote(token_address, SOL_MINT, amount_units, int(SWAP_SLIPPAGE_PCT * 100))
        return execute_jupiter_swap(quote) is not None if quote else False
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
        logger.error(f"Sell error: {e}")
        return False

# -- DEXSCREENER API --
def fetch_boosted_tokens(endpoint="latest"):
    try:
        resp = requests.get(f"https://api.dexscreener.com/token-boosts/{endpoint}/v1", timeout=15)
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

def fetch_pair_by_address(token_address):
    try:
        resp = requests.get(
            f"https://api.dexscreener.com/latest/dex/search?q={token_address}", timeout=10
        )
        if resp.status_code == 429:
            return None
        pairs = resp.json().get("pairs", [])
        return pairs[0] if pairs else None
    except Exception as e:
        logger.error(f"fetch_pair_by_address error: {e}")
        return None

def fetch_dex_pairs(query):
    try:
        resp = requests.get(
            f"https://api.dexscreener.com/latest/dex/search?q={query}", timeout=15
        )
        if resp.status_code == 429:
            return []
        return resp.json().get("pairs", [])
    except Exception as e:
        logger.error(f"fetch_dex_pairs error: {e}")
        return []

def fetch_pair_price(chain, pair_address):
    try:
        resp = requests.get(
            f"https://api.dexscreener.com/latest/dex/pairs/{chain}/{pair_address}", timeout=5
        )
        data = resp.json().get("pair", {})
        price = float(data.get("priceUsd", 0))
        return price if price > 0 else None
    except Exception as e:
        logger.error(f"fetch_pair_price error: {e}")
        return None

# -- SECURITY CHECK --
def get_token_security(chain_id, token_address):
    try:
        resp = requests.get(
            f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}",
            params={"contract_addresses": token_address}, timeout=12
        )
        data = resp.json()
        if data.get("code") != 1:
            return None
        return data.get("result", {}).get(token_address.lower())
    except Exception as e:
        logger.error(f"Security check error: {e}")
        return None

def is_token_safe(security_data):
    if not security_data:
        return False, "No security data"
    try:
        if security_data.get("is_honeypot") == "1":
            return False, "Honeypot"
        buy_tax = float(security_data.get("buy_tax", "100"))
        sell_tax = float(security_data.get("sell_tax", "100"))
        if buy_tax > MAX_BUY_TAX:
            return False, f"Buy tax {buy_tax}%"
        if sell_tax > MAX_SELL_TAX:
            return False, f"Sell tax {sell_tax}%"
        if security_data.get("is_mintable") == "1":
            return False, "Mintable"
        if security_data.get("can_take_back_ownership") == "1":
            return False, "Owner reclaim risk"
        return True, "OK"
    except Exception as e:
        return False, f"Parse error: {e}"

# -- BLACKLIST --
def is_blacklisted(token_addr):
    if token_addr in exit_blacklist:
        elapsed = (time.time() - exit_blacklist[token_addr]) / 3600
        if elapsed < BLACKLIST_AFTER_EXIT_HOURS:
            return True
        del exit_blacklist[token_addr]
    return False

def add_to_blacklist(token_addr):
    exit_blacklist[token_addr] = time.time()

def clean_blacklist():
    now = time.time()
    expired = [a for a, t in list(exit_blacklist.items())
               if (now - t) / 3600 >= BLACKLIST_AFTER_EXIT_HOURS]
    for a in expired:
        exit_blacklist.pop(a, None)

# -- FILTER --
def filter_pairs(pairs):
    now_ms = int(time.time() * 1000)
    valid = []
    for pair in pairs:
        try:
            chain = pair.get("chainId", "").lower()
            if chain not in TARGET_CHAINS:
                continue
            price = float(pair.get("priceUsd", 0) or 0)
            if price < MIN_PRICE_USD:
                continue
            liq = float((pair.get("liquidity") or {}).get("usd", 0) or 0)
            vol = float((pair.get("volume") or {}).get("h24", 0) or 0)
            m5  = float((pair.get("priceChange") or {}).get("m5", 0) or 0)
            m1  = float((pair.get("priceChange") or {}).get("m1", 0) or 0)
            h1  = float((pair.get("priceChange") or {}).get("h1", 0) or 0)
            h6  = float((pair.get("priceChange") or {}).get("h6", 0) or 0)
            if liq < MIN_LIQUIDITY: continue
            if vol < MIN_VOLUME: continue
            if m5 < MIN_CHANGE: continue
            if m1 < MIN_M1_CHANGE: continue
            if m1 > MAX_M1_CHANGE: continue
            created = int(pair.get("pairCreatedAt", 0) or 0)
            age_hours = (now_ms - created) / 3_600_000 if created else 999
            if age_hours < MIN_AGE_HOURS: continue
            if age_hours > MAX_AGE_HOURS: continue
            vol_m5 = float((pair.get("volume") or {}).get("m5", 0) or 0)
            vol_h1 = float((pair.get("volume") or {}).get("h1", 0) or 0)
            if vol_h1 > 0:
                avg_5min = vol_h1 / 12
                if avg_5min > 0 and vol_m5 < avg_5min * VOL_ACCEL_MULTIPLIER:
                    continue
            if h1 < 0 and m5 < 5:
                continue
            pair["_liq"] = liq
            pair["_vol"] = vol
            pair["_m5"]  = m5
            pair["_m1"]  = m1
            pair["_h1"]  = h1
            pair["_h6"]  = h6
            pair["_age_hours"] = age_hours
            pair["_price"]     = price
            pair["_chain"]     = chain
            valid.append(pair)
        except Exception as e:
            logger.debug(f"Filter error: {e}")
            continue
    logger.info(f"Filter: {len(pairs)} in → {len(valid)} passed")
    return valid

# -- SCORING --
def calculate_pair_score(pair, is_safe):
    try:
        liq = pair.get("_liq", 0)
        vol = pair.get("_vol", 0)
        m5  = pair.get("_m5", 0)
        m1  = pair.get("_m1", 0)
        age = pair.get("_age_hours", 0)

        if liq < 200000:
            liq_score = 0
        elif liq < 2_000_000:
            liq_score = min((liq - 200000) / 1_800_000, 1.0) * 20
        else:
            liq_score = max(20 - (liq - 2_000_000) / 1_000_000, 10)

        vol_liq = vol / liq if liq > 0 else 0
        if vol_liq < 0.1:
            vol_score = 0
        elif vol_liq > 10:
            vol_score = 5
        else:
            vol_score = min(vol_liq / 3, 1.0) * 15

        if m5 < 3:
            momentum_score = 0
        elif m5 <= 15:
            momentum_score = min((m5 - 3) / 12, 1.0) * 25
        else:
            momentum_score = max(25 - (m5 - 15) * 1.5, 5)

        if m1 <= 0:
            m1_score = 0
        elif m1 <= 5:
            m1_score = min(m1 / 5, 1.0) * 20
        else:
            m1_score = max(20 - (m1 - 5) * 2, 5)

        safety_score = 15 if is_safe else 0
        age_bonus = 5 if 2 <= age <= 48 else (2 if 1 <= age < 2 or 48 < age <= 72 else 0)

        return round(min(liq_score + vol_score + momentum_score + m1_score + safety_score + age_bonus, 100.0), 2)
    except Exception as e:
        logger.error(f"Score error: {e}")
        return 0.0

# -- MOMENTUM CONFIRMATION --
def confirm_momentum(pair, required=MOMENTUM_REQUIRED):
    token_addr = pair.get("baseToken", {}).get("address", "").lower()
    m1 = pair.get("_m1", 0)
    now = time.time()
    history = momentum_history.get(token_addr, [])
    history = [(t, v) for t, v in history if now - t < 300]
    history.append((now, m1))
    momentum_history[token_addr] = history
    if len(history) < required:
        return False
    return all(v > 0 for _, v in history[-required:])
# -- ENTRY --
def simulate_buy(pair, score):
    token_addr = pair.get("baseToken", {}).get("address", "").lower()
    pair_addr  = pair.get("pairAddress", "")
    chain      = pair.get("_chain", pair.get("chainId"))

    if not token_addr or not pair_addr:
        return None

    now = time.time()
    with trade_lock:
        if len(active_trades) >= MAX_OPEN_TRADES:
            return None
        if token_addr in recent and (now - recent[token_addr]) < 1800:
            return None
        if token_addr in active_trades:
            return None
        if is_blacklisted(token_addr):
            return None
        try:
            price = pair.get("_price", float(pair.get("priceUsd", 0) or 0))
            if price <= 0:
                return None
            entry_price = price * (1 + SLIPPAGE_PCT / 100)
            trade_usd   = MAX_TRADE_SIZE
            quantity    = trade_usd / entry_price
            if not PAPER_MODE:
                if not buy_token(chain, token_addr, trade_usd):
                    logger.error(f"Buy failed: {pair['baseToken']['symbol']}")
                    return None
            trade = {
                "token":          pair["baseToken"]["symbol"],
                "token_address":  token_addr,
                "pair_address":   pair_addr,
                "chain":          chain,
                "entry_price":    entry_price,
                "amount_usd":     trade_usd,
                "quantity":       quantity,
                "remaining_qty":  quantity,
                "total_spent":    trade_usd,
                "partial_profit": 0.0,
                "scaled_out":     False,
                "tp1_price":      None,
                "timestamp":      now,
                "highest_price":  entry_price,
                "score":          score,
                "m5_at_entry":    pair.get("_m5", 0),
                "m1_at_entry":    pair.get("_m1", 0),
            }
            active_trades[token_addr] = trade
            recent[token_addr] = now
            logger.info(
                f"✅ BUY: {trade['token']} @ ${entry_price:.8f} | "
                f"Score:{score} m5:{pair.get('_m5',0):.1f}% m1:{pair.get('_m1',0):.1f}% | Chain:{chain}"
            )
            return trade
        except Exception as e:
            logger.error(f"Entry error: {e}")
            return None

# -- MONITOR --
def monitor_positions_fast():
    global tp_hits, sl_hits, trailing_hits, timeout_exits, total_exits
    global daily_pnl_usd, halt_trading, partial_tp_hits
    global session_best_pct, session_worst_pct

    closed = []
    now = time.time()

    with trade_lock:
        items = list(active_trades.items())

    for token_addr, trade in items:
        try:
            current_price = fetch_pair_price(trade["chain"], trade["pair_address"])
            if not current_price or current_price <= 0:
                continue

            entry_price = trade["entry_price"]
            pct_change  = ((current_price - entry_price) / entry_price) * 100

            with trade_lock:
                if token_addr in active_trades:
                    if current_price > active_trades[token_addr]["highest_price"]:
                        active_trades[token_addr]["highest_price"] = current_price
                    trade = active_trades[token_addr]

            # Partial TP
            if not trade.get("scaled_out") and pct_change >= PARTIAL_TP_PCT:
                partial_qty    = trade["quantity"] * PARTIAL_TP_RATIO
                partial_profit = partial_qty * current_price - partial_qty * entry_price
                if not PAPER_MODE:
                    units = int(partial_qty * (10**6 if trade["chain"] == "solana" else 10**18))
                    sell_token(trade["chain"], token_addr, units)
                with trade_lock:
                    if token_addr in active_trades:
                        active_trades[token_addr]["scaled_out"]     = True
                        active_trades[token_addr]["tp1_price"]      = current_price
                        active_trades[token_addr]["remaining_qty"]  -= partial_qty
                        active_trades[token_addr]["partial_profit"] += partial_profit
                        trade = active_trades[token_addr]
                partial_tp_hits += 1
                logger.info(f"📈 PARTIAL TP: {trade['token']} +{pct_change:.2f}%")
                send_discord_alert(f"Partial TP: {trade['token']} +{pct_change:.2f}%")
                continue

            exit_reason = None
            if pct_change >= TAKE_PROFIT_PCT:
                exit_reason = "TAKE_PROFIT"
            elif pct_change <= STOP_LOSS_PCT:
                exit_reason = "STOP_LOSS"
            elif pct_change <= EMERGENCY_STOP_PCT:
                exit_reason = "EMERGENCY_STOP"

            if exit_reason is None and TRAILING_STOP_ENABLED and trade.get("scaled_out"):
                highest = trade.get("highest_price", entry_price)
                profit_from_high = ((highest - entry_price) / entry_price) * 100
                if profit_from_high >= TRAILING_ACTIVATION_PCT:
                    trail_stop = highest * (1 - TRAILING_DISTANCE_PCT / 100)
                    if current_price <= trail_stop:
                        exit_reason = "TRAILING_STOP"

            if exit_reason is None and MAX_HOLD_MINUTES > 0:
                held_minutes = (now - trade["timestamp"]) / 60
                if held_minutes >= MAX_HOLD_MINUTES:
                    exit_reason = "MAX_HOLD"

            if exit_reason:
                if not PAPER_MODE:
                    sell_qty = trade.get("remaining_qty", trade["quantity"])
                    units = int(sell_qty * (10**6 if trade["chain"] == "solana" else 10**18))
                    sell_token(trade["chain"], token_addr, units)

                with trade_lock:
                    closed_trade = active_trades.pop(token_addr, None)
                if closed_trade is None:
                    continue

                add_to_blacklist(token_addr)
                remaining_qty  = closed_trade.get("remaining_qty", closed_trade["quantity"])
                partial_profit = closed_trade.get("partial_profit", 0.0)
                final_profit   = remaining_qty * current_price - remaining_qty * entry_price
                total_pnl_usd  = partial_profit + final_profit
                total_pnl_pct  = (total_pnl_usd / closed_trade["total_spent"]) * 100 if closed_trade["total_spent"] else 0

                closed_trade.update({
                    "exit_price":   current_price,
                    "exit_reason":  exit_reason,
                    "pnl_pct":      round(total_pnl_pct, 2),
                    "pnl_usd":      round(total_pnl_usd, 2),
                    "hold_minutes": round((now - closed_trade["timestamp"]) / 60, 1),
                })
                closed.append(closed_trade)

                total_exits   += 1
                daily_pnl_usd += total_pnl_usd

                if total_pnl_pct > session_best_pct:
                    session_best_pct = total_pnl_pct
                if total_pnl_pct < session_worst_pct:
                    session_worst_pct = total_pnl_pct

                if exit_reason in ("TAKE_PROFIT", "TRAILING_STOP"):
                    tp_hits += 1
                    if exit_reason == "TRAILING_STOP":
                        trailing_hits += 1
                elif "STOP" in exit_reason:
                    sl_hits += 1
                elif exit_reason == "MAX_HOLD":
                    timeout_exits += 1

                win_rate = (tp_hits / total_exits * 100) if total_exits else 0
                emoji = "🟢" if total_pnl_usd >= 0 else "🔴"
                logger.info(
                    f"{emoji} {exit_reason}: {closed_trade['token']} "
                    f"PNL {total_pnl_pct:.2f}% (${total_pnl_usd:.2f}) | "
                    f"WR:{win_rate:.1f}% [{tp_hits}TP/{sl_hits}SL/{timeout_exits}TO/{total_exits}] | "
                    f"Daily:${daily_pnl_usd:.2f}"
                )
                if daily_pnl_usd <= -abs(MAX_DAILY_LOSS_USD):
                    logger.warning("🚨 Daily loss limit! Halting entries.")
                    halt_trading = True

        except Exception as e:
            logger.error(f"Monitor error [{trade.get('token','?')}]: {e}")

    return closed

def fast_monitor_loop():
    logger.info(f"Monitor thread started (interval={FAST_MONITOR_INTERVAL}s)")
    while True:
        try:
            closed = monitor_positions_fast()
            for ct in closed:
                win_rate = f"{tp_hits}/{total_exits} ({tp_hits/total_exits*100:.1f}%)" if total_exits else "N/A"
                embed = {
                    "title": f"{'🟢' if ct['pnl_usd'] >= 0 else '🔴'} {ct['exit_reason']}: {ct['token']}",
                    "color": 0x00FF00 if ct["pnl_usd"] >= 0 else 0xFF0000,
                    "fields": [
                        {"name": "Chain",     "value": ct["chain"],                    "inline": True},
                        {"name": "Entry",     "value": f"${ct['entry_price']:.8f}",    "inline": True},
                        {"name": "Exit",      "value": f"${ct['exit_price']:.8f}",     "inline": True},
                        {"name": "P&L %",     "value": f"{ct['pnl_pct']}%",            "inline": True},
                        {"name": "P&L $",     "value": f"${ct['pnl_usd']:.2f}",        "inline": True},
                        {"name": "Hold",      "value": f"{ct['hold_minutes']}m",       "inline": True},
                        {"name": "Win Rate",  "value": win_rate,                       "inline": False},
                        {"name": "Daily P&L", "value": f"${daily_pnl_usd:.2f}",        "inline": True},
                    ],
                }
                send_discord_alert("Trade closed", embed)
        except Exception as e:
            logger.error(f"Monitor loop error: {e}")
        time.sleep(FAST_MONITOR_INTERVAL)

# -- SCANNER --
SEARCH_TERMS = [
    "pepe", "shib", "doge", "floki", "inu", "baby", "moon", "cat",
    "ai", "gpt", "bot", "based", "chad", "pump", "elon", "king", "rocket", "safe",
]

def scanner_loop():
    global scan_cycle_count, daily_pnl_usd, daily_reset_time, halt_trading
    logger.info(
        f"🚀 DEX SCALPER PRO v4 | Chains:{TARGET_CHAINS} | "
        f"Paper:{PAPER_MODE} | TP:{TAKE_PROFIT_PCT}% SL:{STOP_LOSS_PCT}%"
    )
    last_clean = time.time()

    while True:
        try:
            if time.time() - daily_reset_time > 86400:
                daily_pnl_usd    = 0.0
                halt_trading     = False
                daily_reset_time = time.time()
                logger.info("📅 Daily P&L reset.")

            if halt_trading:
                logger.warning("⏸ Halted — daily loss limit.")
                time.sleep(SCAN_INTERVAL_SECONDS)
                continue

            scan_cycle_count += 1
            all_pairs = []
            seen_addresses = set()

            boosted = fetch_boosted_tokens("latest")
            for b in [x for x in boosted if x.get("chainId", "").lower() in TARGET_CHAINS][:25]:
                if b.get("tokenAddress"):
                    pair = fetch_pair_by_address(b["tokenAddress"])
                    if pair and pair.get("pairAddress") not in seen_addresses:
                        seen_addresses.add(pair["pairAddress"])
                        all_pairs.append(pair)
                    time.sleep(0.15)

            if len(all_pairs) < 40:
                for term in SEARCH_TERMS:
                    for p in fetch_dex_pairs(term):
                        addr = p.get("pairAddress")
                        if addr and addr not in seen_addresses:
                            seen_addresses.add(addr)
                            all_pairs.append(p)
                    time.sleep(0.2)
                    if len(all_pairs) >= 80:
                        break

            valid = filter_pairs(all_pairs)
            valid.sort(
                key=lambda x: x.get("_m5", 0) * (x.get("_vol", 0) / max(x.get("_liq", 1), 1)),
                reverse=True
            )

            top = []
            seen_tokens = set()
            for p in valid:
                addr = p.get("baseToken", {}).get("address", "").lower()
                if addr and addr not in seen_tokens:
                    seen_tokens.add(addr)
                    top.append(p)
                if len(top) >= 15:
                    break

            for pair in top:
                if halt_trading or len(active_trades) >= MAX_OPEN_TRADES:
                    break

                token_addr = pair["baseToken"]["address"]
                chain      = pair["_chain"]
                numeric_id = CHAIN_ID_MAP.get(chain)
                if not numeric_id:
                    continue

                security = get_token_security(numeric_id, token_addr)
                safe, reason = is_token_safe(security)
                if not safe:
                    logger.info(f"⛔ UNSAFE: {pair['baseToken']['symbol']} — {reason}")
                    continue

                score = calculate_pair_score(pair, safe)
                if score < MIN_SCORE:
                    logger.info(f"Score {score:.1f} < {MIN_SCORE} — skip {pair['baseToken']['symbol']}")
                    continue

                if not confirm_momentum(pair):
                    logger.info(f"No momentum — skip {pair['baseToken']['symbol']}")
                    continue

                trade = simulate_buy(pair, score)
                if trade:
                    send_discord_alert("New trade", {
                        "title": f"🟡 BUY: {trade['token']} on {chain}",
                        "color": 0xFFFF00,
                        "fields": [
                            {"name": "Price",  "value": f"${trade['entry_price']:.8f}", "inline": True},
                            {"name": "Score",  "value": str(score),                      "inline": True},
                            {"name": "M5",     "value": f"{pair['_m5']:.2f}%",           "inline": True},
                            {"name": "M1",     "value": f"{pair['_m1']:.2f}%",           "inline": True},
                        ],
                    })
                time.sleep(0.5)

            if time.time() - last_clean > 600:
                clean_blacklist()
                stale = [a for a, h in list(momentum_history.items())
                         if h and time.time() - h[-1][0] > 3600]
                for a in stale:
                    momentum_history.pop(a, None)
                last_clean = time.time()

            if scan_cycle_count % 5 == 0 and total_exits > 0:
                wr = tp_hits / total_exits * 100
                logger.info(
                    f"📊 WR:{wr:.1f}% TP:{tp_hits} SL:{sl_hits} "
                    f"Trail:{trailing_hits} TO:{timeout_exits} Total:{total_exits} | "
                    f"Daily:${daily_pnl_usd:.2f} | Best:{session_best_pct:.1f}% Worst:{session_worst_pct:.1f}%"
                )

            time.sleep(SCAN_INTERVAL_SECONDS)

        except Exception as e:
            logger.error(f"Scanner error: {e}", exc_info=True)
            time.sleep(30)

# -- FLASK --
app = Flask(__name__)

@app.route("/health")
def health():
    return "OK", 200

@app.route("/")
def status():
    wr = f"{tp_hits}/{total_exits} ({tp_hits/total_exits*100:.1f}%)" if total_exits else "N/A"
    return jsonify({
        "status":        "running",
        "paper_mode":    PAPER_MODE,
        "open_trades":   len(active_trades),
        "win_rate":      wr,
        "tp_hits":       tp_hits,
        "sl_hits":       sl_hits,
        "trailing_hits": trailing_hits,
        "timeout_exits": timeout_exits,
        "total_exits":   total_exits,
        "daily_pnl":     round(daily_pnl_usd, 2),
        "halted":        halt_trading,
        "scan_cycle":    scan_cycle_count,
        "best_trade":    f"{session_best_pct:.2f}%",
        "worst_trade":   f"{session_worst_pct:.2f}%",
        "active_trades": {
            addr: {
                "token":      t["token"],
                "chain":      t["chain"],
                "hold_min":   round((time.time() - t["timestamp"]) / 60, 1),
                "scaled_out": t["scaled_out"],
            }
            for addr, t in active_trades.items()
        },
    })

@app.route("/trades")
def trades():
    return jsonify(list(active_trades.values()))

# -- MAIN --
if __name__ == "__main__":
    if not DISCORD_WEBHOOK_URL:
        logger.warning("⚠️  No Discord webhook set.")
    if not PAPER_MODE:
        logger.warning("🔴 LIVE MODE — real funds at risk!")
        if "solana" in TARGET_CHAINS and SOLANA_PRIVATE_KEY:
            if init_solana():
                logger.info("✅ Solana ready")
            else:
                logger.error("❌ Solana init failed")
        if not WALLET_PRIVATE_KEY and any(c != "solana" for c in TARGET_CHAINS):
            logger.error("❌ EVM private key required for live mode")
            sys.exit(1)
    else:
        logger.info("📄 PAPER MODE active")

    threading.Thread(target=fast_monitor_loop, daemon=True).start()
    threading.Thread(target=scanner_loop, daemon=True).start()

    port = int(os.getenv("PORT", "8080"))
    logger.info(f"🌐 Flask on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
```
