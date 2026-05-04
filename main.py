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

# ── CONFIG ────────────────────────────────────────────────────────────────
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

# ── GLOBAL STATE ──────────────────────────────────────────────────────────
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
