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
from web3.middleware.geth_poa import geth_poa_middleware

# Solana imports (graceful fallback if not installed)
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
    logger = logging.getLogger("scalper")
    logger.warning("Solana libraries not installed. Solana trades will be skipped.")

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
# Configuration
# ----------------------------------------------------------------------
PAPER_MODE = os.getenv("PAPER_MODE", "true").lower() == "true"
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

TARGET_CHAINS = [c.strip().lower() for c in os.getenv("TARGET_CHAINS", "bsc,base").split(",") if c.strip()]

MAX_TRADE_SIZE = float(os.getenv("MAX_TRADE_SIZE", "100.0"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "-1.5"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "3.0"))
TRAILING_STOP_ENABLED = os.getenv("TRAILING_STOP_ENABLED", "true").lower() == "true"
TRAILING_ACTIVATION_PCT = float(os.getenv("TRAILING_ACTIVATION_PCT", "2.0"))
TRAILING_DISTANCE_PCT = float(os.getenv("TRAILING_DISTANCE_PCT", "0.5"))
MAX_HOLD_MINUTES = float(os.getenv("MAX_HOLD_MINUTES", "0"))
PULLBACK_ENTRY_PCT = float(os.getenv("PULLBACK_ENTRY_PCT", "0"))
SLIPPAGE_PCT = float(os.getenv("SLIPPAGE_PCT", "0.3"))
SWAP_SLIPPAGE_PCT = float(os.getenv("SWAP_SLIPPAGE_PCT", "1.0"))

MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "50000.0"))
MIN_VOLUME = float(os.getenv("MIN_VOLUME", "15000.0"))
MIN_CHANGE = float(os.getenv("MIN_CHANGE", "1.0"))
MIN_AGE_HOURS = float(os.getenv("MIN_AGE_HOURS", "0.0"))
MIN_PRICE_USD = float(os.getenv("MIN_PRICE_USD", "0.0001"))
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "60"))
FAST_MONITOR_INTERVAL = int(os.getenv("FAST_MONITOR_INTERVAL", "5"))
MIN_SCORE = float(os.getenv("MIN_SCORE", "70.0"))

BLACKLIST_AFTER_EXIT_HOURS = float(os.getenv("BLACKLIST_AFTER_EXIT_HOURS", "4.0"))

# Wallet & RPC for EVM chains
WALLET_PRIVATE_KEY = os.getenv("WALLET_PRIVATE_KEY", "")
RPC_URLS = {
    "ethereum": os.getenv("RPC_URL_ETHEREUM", ""),
    "bsc": os.getenv("RPC_URL_BSC", ""),
    "base": os.getenv("RPC_URL_BASE", ""),
}

# Solana specific
SOLANA_PRIVATE_KEY = os.getenv("SOLANA_PRIVATE_KEY", "")
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

# Jupiter API endpoint
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
        w3.middleware_onion.inject(geth_poa_middleware, layer=0)
    if not w3.is_connected():
        logger.error(f"Failed to connect to {chain} RPC")
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

# ----------------------------------------------------------------------
# Solana Jupiter Helpers
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
    # EVM
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
# API Functions
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
# Filtering
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
            m5 = float(pair.get("priceChange", {}).get("m5", 0))
            created = int(pair.get("pairCreatedAt", 0))
            age_hours = (now_ms - created) / 3600000 if created else 0
            if liq < MIN_LIQUIDITY or vol < MIN_VOLUME or m5 < MIN_CHANGE:
                continue
            if MIN_AGE_HOURS > 0 and age_hours < MIN_AGE_HOURS:
                continue
            pair["_liq"] = liq
            pair["_vol"] = vol
            pair["_m5"] = m5
            pair["_age_hours"] = age_hours
            pair["_price"] = price
            pair["_chain"] = chain
            valid.append(pair)
        except:
            continue
    logger.info(f"Filter summary: {len(pairs)} input, {len(valid)} passed for {TARGET_CHAINS}")
    return valid

# ----------------------------------------------------------------------
# Security & Score
# ----------------------------------------------------------------------
def get_token_security(chain_id: int, token_address: str) -> Optional[dict]:
    url = f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}"
    try:
        resp = requests.get(url, params={"contract_addresses": token_address}, timeout=10)
        data = resp.json()
        if data.get("code") != 1:
            return None
        return data.get("result", {}).get(token_address.lower())
    except:
        return None

def is_token_safe(security_data: Optional[dict]) -> bool:
    if not security_data:
        return False
    try:
        return (security_data.get("is_honeypot") == "0" and
                float(security_data.get("buy_tax", "100")) <= 10 and
                float(security_data.get("sell_tax", "100")) <= 10)
    except:
        return False

def calculate_pair_score(pair: dict, is_safe: bool) -> float:
    try:
        liq = pair.get("_liq", 0)
        vol = pair.get("_vol", 0)
        m5 = pair.get("_m5", 0)
        liq_score = min(liq / 100000, 1) * 30
        vol_score = min(vol / 50000, 1) * 20
        momentum_score = min(abs(m5) / 10, 1) * 30
        safety_score = 20 if is_safe else 0
        return round(liq_score + vol_score + momentum_score + safety_score, 2)
    except:
        return 0.0

# ----------------------------------------------------------------------
# Blacklist
# ----------------------------------------------------------------------
def is_blacklisted(token_addr: str) -> bool:
    if token_addr in exit_blacklist:
        if (time.time() - exit_blacklist[token_addr]) / 3600 < BLACKLIST_AFTER_EXIT_HOURS:
            return True
        else:
            del exit_blacklist[token_addr]
    return False

def add_to_blacklist(token_addr: str):
    exit_blacklist[token_addr] = time.time()

def clean_blacklist():
    now = time.time()
    expired = [a for a, t in exit_blacklist.items() if (now - t) / 3600 >= BLACKLIST_AFTER_EXIT_HOURS]
    for a in expired:
        del exit_blacklist[a]

# ----------------------------------------------------------------------
# Trading Entry
# ----------------------------------------------------------------------
def simulate_buy(pair: dict) -> Optional[dict]:
    token_addr = pair.get("baseToken", {}).get("address", "").lower()
    pair_addr = pair.get("pairAddress", "")
    chain = pair.get("_chain", pair.get("chainId"))
    if not token_addr or not pair_addr:
        return None

    now = time.time()
    with trade_lock:
        if token_addr in recent and (now - recent[token_addr]) < 1800:
            return None
        if token_addr in active_trades:
            return None
        if is_blacklisted(token_addr):
            return None
        try:
            price = pair.get("_price", float(pair.get("priceUsd", 0)))
            if price <= 0:
                return None
            trade_usd = MAX_TRADE_SIZE
            entry_price = price * (1 + SLIPPAGE_PCT / 100)
            quantity = trade_usd / entry_price

            if not PAPER_MODE:
                if not buy_token(chain, token_addr, trade_usd):
                    return None

            trade = {
                "token": pair["baseToken"]["symbol"],
                "token_address": token_addr,
                "pair_address": pair_addr,
                "chain": chain,
                "entry_price": entry_price,
                "amount_usd": trade_usd,
                "quantity": quantity,
                "timestamp": now,
                "highest_price": entry_price,
            }
            active_trades[token_addr] = trade
            recent[token_addr] = now
            logger.info(f"BUY: {trade['token']} qty={quantity:.6f} at ${entry_price:.8f} on {chain}")
            return trade
        except Exception as e:
            logger.error(f"Entry error: {e}")
            return None

# ----------------------------------------------------------------------
# Fast Monitor (with emergency stop)
# ----------------------------------------------------------------------
def monitor_positions_fast() -> List[dict]:
    closed = []
    now = time.time()
    with trade_lock:
        items = list(active_trades.items())

    for token_addr, trade in items:
        try:
            chain = trade["chain"]
            current_price = fetch_pair_price(chain, trade["pair_address"])
            if current_price is None or current_price <= 0:
                continue

            entry_price = trade["entry_price"]
            pct_change = ((current_price - entry_price) / entry_price) * 100

            with trade_lock:
                if token_addr in active_trades:
                    if current_price > active_trades[token_addr]["highest_price"]:
                        active_trades[token_addr]["highest_price"] = current_price

            exit_reason = None

            # Normal TP/SL
            if pct_change >= TAKE_PROFIT_PCT:
                exit_reason = "TAKE_PROFIT"
            elif pct_change <= STOP_LOSS_PCT:
                exit_reason = "STOP_LOSS"

            # Trailing
            if exit_reason is None and TRAILING_STOP_ENABLED:
                highest = trade.get("highest_price", entry_price)
                if ((highest - entry_price) / entry_price) * 100 >= TRAILING_ACTIVATION_PCT:
                    trailing_stop = highest * (1 - TRAILING_DISTANCE_PCT / 100)
                    if current_price <= trailing_stop:
                        exit_reason = "TRAILING_STOP"

            # Max Hold
            if exit_reason is None and MAX_HOLD_MINUTES > 0:
                if (now - trade["timestamp"]) / 60 >= MAX_HOLD_MINUTES:
                    exit_reason = "MAX_HOLD"

            # EMERGENCY HARD STOP – no matter what
            if pct_change <= -5.0 and exit_reason is None:
                exit_reason = "EMERGENCY_STOP"

            if exit_reason:
                with trade_lock:
                    closed_trade = active_trades.pop(token_addr, None)
                if closed_trade is None:
                    continue   # already closed (prevents duplicate)

                # Live sell
                if not PAPER_MODE:
                    if chain == "solana":
                        amount_units = int(trade["quantity"] * 10**6)
                    else:
                        amount_units = int(trade["quantity"] * 10**18)
                    sell_token(chain, token_addr, amount_units)

                closed_trade["exit_price"] = current_price
                closed_trade["exit_reason"] = exit_reason
                closed_trade["pnl_pct"] = round(pct_change, 2)
                closed_trade["pnl_usd"] = round(
                    trade["quantity"] * current_price - trade["amount_usd"], 2
                )
                closed.append(closed_trade)
                add_to_blacklist(token_addr)
                logger.info(f"{exit_reason}: {trade['token']} at {pct_change:.2f}%")
        except Exception as e:
            logger.error(f"Monitor error for {trade.get('token','')}: {e}")

    return closed

def fast_monitor_loop():
    logger.info(f"Monitor thread started ({FAST_MONITOR_INTERVAL}s)")
    while True:
        try:
            closed = monitor_positions_fast()
            for ct in closed:
                embed = {
                    "title": f"{'GREEN' if ct['pnl_usd'] >= 0 else 'RED'} {ct['exit_reason']}: {ct['token']}",
                    "fields": [
                        {"name": "Entry", "value": f"${ct['entry_price']:.8f}", "inline": True},
                        {"name": "Exit", "value": f"${ct['exit_price']:.8f}", "inline": True},
                        {"name": "P&L %", "value": f"{ct['pnl_pct']}%", "inline": True},
                        {"name": "P&L $", "value": f"${ct['pnl_usd']:.2f}", "inline": True},
                    ],
                }
                send_discord_alert("Trade closed", embed)
        except Exception as e:
            logger.error(f"Monitor loop error: {e}")
        time.sleep(FAST_MONITOR_INTERVAL)

# ----------------------------------------------------------------------
# Scanner Loop
# ----------------------------------------------------------------------
SEARCH_TERMS = ["pepe", "shib", "doge", "elon", "floki", "moon", "inu", "baby", "pump", "king", "rocket", "cat", "ai", "gpt", "bot", "safe", "based", "chad"]

def scanner_loop():
    global scan_cycle_count
    logger.info(f"SCALPER v10 – {TARGET_CHAINS} – Paper:{PAPER_MODE}")
    last_clean = time.time()
    while True:
        try:
            scan_cycle_count += 1
            all_pairs = []

            boosted = fetch_boosted_tokens("latest")
            if boosted:
                chain_boosted = [b for b in boosted if b.get("chainId") in TARGET_CHAINS]
                for b in chain_boosted[:20]:
                    if b.get("tokenAddress"):
                        pair = fetch_pair_by_address(b["tokenAddress"])
                        if pair:
                            all_pairs.append(pair)
                        time.sleep(0.1)

            if len(all_pairs) < 30:
                seen = set(p.get("pairAddress") for p in all_pairs)
                for term in SEARCH_TERMS[:10]:
                    for p in fetch_dex_pairs(term):
                        if p.get("pairAddress") not in seen:
                            seen.add(p["pairAddress"])
                            all_pairs.append(p)
                    time.sleep(0.2)

            valid = filter_pairs(all_pairs)
            valid.sort(key=lambda x: x.get("_m5", 0), reverse=True)
            top = []
            seen_tokens = set()
            for p in valid:
                addr = p.get("baseToken", {}).get("address", "").lower()
                if addr not in seen_tokens:
                    seen_tokens.add(addr)
                    top.append(p)
                if len(top) >= 10:
                    break

            for pair in top:
                token_addr = pair["baseToken"]["address"]
                chain = pair["_chain"]
                numeric_chain = CHAIN_ID_MAP.get(chain)
                if not numeric_chain:
                    continue
                security = get_token_security(numeric_chain, token_addr)
                safe = is_token_safe(security)
                if not safe:
                    continue
                score = calculate_pair_score(pair, safe)
                if score < MIN_SCORE:
                    logger.info(f"Score {score} < {MIN_SCORE}, skip")
                    continue
                trade = simulate_buy(pair)
                if trade:
                    embed = {
                        "title": f"BUY: {trade['token']} on {chain} (Score: {score})",
                        "fields": [
                            {"name": "Price", "value": f"${trade['entry_price']:.8f}", "inline": True},
                            {"name": "Amount", "value": f"${trade['amount_usd']:.2f}", "inline": True},
                        ],
                    }
                    send_discord_alert("New trade", embed)

            if time.time() - last_clean > 600:
                clean_blacklist()
                last_clean = time.time()
            if scan_cycle_count % 5 == 0:
                logger.info(f"Cycle {scan_cycle_count}, open: {len(active_trades)}")
            time.sleep(SCAN_INTERVAL_SECONDS)
        except Exception as e:
            logger.error(f"Scanner loop error: {e}")
            time.sleep(30)

# ----------------------------------------------------------------------
# Flask
# ----------------------------------------------------------------------
app = Flask(__name__)

@app.route("/health")
def health():
    return "OK", 200

@app.route("/")
def status():
    return jsonify({"status": "running", "paper": PAPER_MODE, "trades": len(active_trades)})

# ----------------------------------------------------------------------
# Entry Point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    if not DISCORD_WEBHOOK_URL:
        logger.warning("No Discord URL")
    if not PAPER_MODE:
        if "solana" in TARGET_CHAINS and SOLANA_PRIVATE_KEY:
            init_solana()
        if not WALLET_PRIVATE_KEY and any(c != "solana" for c in TARGET_CHAINS):
            logger.error("EVM private key required for live mode")
            sys.exit(1)

    threading.Thread(target=fast_monitor_loop, daemon=True).start()
    threading.Thread(target=scanner_loop, daemon=True).start()
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False) 
