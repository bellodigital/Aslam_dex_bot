# =============================================================================
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
}WETH_ADDRESSES = {
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
