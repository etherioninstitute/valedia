"""DeFi/MetaMask Client - Abundance Agent
EVM chain interaction via web3.py. Read-only by default.
Supports Ethereum, Polygon, Arbitrum, Base for:
- Portfolio balances (ETH + ERC-20 tokens)
- DeFi protocol positions (Uniswap, Lido, Aave)
- Gas price monitoring
- Staking yield tracking (Lido stETH = ~3-4% APR)

Private key stored in vault for future trade execution.
Start READ-ONLY until operator approves writes.
"""
import os, json
from web3 import Web3

# RPC endpoints (free public, upgrade to Alchemy/Infura for speed)
CHAINS = {
    "ethereum": "https://eth.llamarpc.com",
    "polygon": "https://polygon-rpc.com",
    "arbitrum": "https://arb1.arbitrum.io/rpc",
    "base": "https://mainnet.base.org",
}

WALLET = os.environ.get("METAMASK_WALLET", "")

# Key ERC-20 token addresses (Ethereum mainnet)
TOKENS = {
    "USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
    "WETH": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
    "stETH": "0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84",
    "LINK": "0x514910771AF9Ca656af840dff83E8264EcF986CA",
    "UNI": "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984",
}

ERC20_ABI = [{"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"}, {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"}]

def get_web3(chain="ethereum"):
    rpc = CHAINS.get(chain, CHAINS["ethereum"])
    return Web3(Web3.HTTPProvider(rpc))

def get_eth_balance(chain="ethereum"):
    if not WALLET:
        return 0
    w3 = get_web3(chain)
    bal = w3.eth.get_balance(Web3.to_checksum_address(WALLET))
    return w3.from_wei(bal, 'ether')

def get_token_balance(token_addr, chain="ethereum"):
    if not WALLET:
        return 0
    w3 = get_web3(chain)
    contract = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ERC20_ABI)
    bal = contract.functions.balanceOf(Web3.to_checksum_address(WALLET)).call()
    decimals = contract.functions.decimals().call()
    return bal / (10 ** decimals)

def get_gas_price(chain="ethereum"):
    w3 = get_web3(chain)
    return w3.from_wei(w3.eth.gas_price, 'gwei')

def chain_overview():
    lines = ["DeFi/EVM Overview"]
    for chain in ["ethereum", "arbitrum", "base"]:
        try:
            w3 = get_web3(chain)
            block = w3.eth.block_number
            gas = w3.from_wei(w3.eth.gas_price, 'gwei')
            lines.append(f"  {chain}: block {block:,} | gas {gas:.1f} gwei")
        except Exception as e:
            lines.append(f"  {chain}: error - {str(e)[:40]}")
    
    if WALLET:
        lines.append(f"\n  Wallet: {WALLET[:8]}...{WALLET[-6:]}")
        try:
            eth_bal = get_eth_balance()
            lines.append(f"  ETH: {eth_bal:.6f}")
            for name, addr in TOKENS.items():
                try:
                    bal = get_token_balance(addr)
                    if bal > 0.001:
                        lines.append(f"  {name}: {bal:.6f}")
                except:
                    pass
        except Exception as e:
            lines.append(f"  Balance error: {e}")
    else:
        lines.append("  Wallet: Not configured (set METAMASK_WALLET)")
    
    lines.append("")
    lines.append("  DeFi Opportunities:")
    lines.append("    Lido stETH: ~3.5% APR (liquid staking)")
    lines.append("    Aave USDC: ~4-8% APR (lending)")
    lines.append("    Uniswap LP: variable (impermanent loss risk)")
    lines.append("  Status: READ-ONLY (write perms need approval)")
    
    return "\n".join(lines)

if __name__ == "__main__":
    print(chain_overview())
