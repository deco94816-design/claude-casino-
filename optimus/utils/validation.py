# -*- coding: utf-8 -*-
"""Crypto address validation and id helpers (pure; extracted verbatim).

Re-imported into librate_casino so existing call sites are unchanged.
"""

import re
import random
import string


def generate_transaction_id():
    chars = string.ascii_letters + string.digits
    return 'stx' + ''.join(random.choice(chars) for _ in range(80))


def generate_temp_crypto_address(base_address, coin_key):
    """Generate a temporary crypto address based on the base address"""
    # For now, we'll use the base address as-is
    # In a real implementation, you might want to generate sub-addresses
    # For simplicity, we'll append a random suffix to make it unique
    import secrets
    suffix = secrets.token_hex(8)[:16]  # 16 character suffix
    # Format depends on coin type
    if coin_key in ["bitcoin", "litecoin"]:
        # For Bitcoin/Litecoin, we might need a different approach
        # For now, return base address with a note that it's temporary
        return base_address
    elif coin_key in ["ethereum", "usdt_bep20", "usdc_erc20"]:
        # Ethereum addresses are 42 chars, we can't modify them easily
        # Return base address
        return base_address
    elif coin_key == "solana":
        # Solana addresses can be longer
        return base_address
    elif coin_key == "ton":
        # TON addresses are specific format
        return base_address
    elif coin_key == "monero":
        # Monero uses subaddresses
        return base_address
    return base_address


def is_valid_crypto_address(address):
    """Validate cryptocurrency address format"""
    if not address:
        return False, "Unknown"
    
    address = address.strip()
    
    # Bitcoin addresses
    # Legacy: starts with 1 or 3, 26-35 chars
    # P2SH: starts with 3
    # Bech32: starts with bc1, 42-62 chars
    if address.startswith('bc1'):
        if 42 <= len(address) <= 62:
            return True, "Bitcoin"
    elif address.startswith('1') or address.startswith('3'):
        if 26 <= len(address) <= 35:
            return True, "Bitcoin"
    
    # Litecoin addresses
    # Legacy: starts with L or M, 26-34 chars
    # Bech32: starts with ltc1, 42-62 chars
    if address.startswith('ltc1'):
        if 42 <= len(address) <= 62:
            return True, "Litecoin"
    elif address.startswith('L') or address.startswith('M'):
        if 26 <= len(address) <= 34:
            return True, "Litecoin"
    
    # Ethereum addresses (42 chars, starts with 0x, hex)
    if address.startswith('0x') and len(address) == 42:
        if re.match(r'^0x[a-fA-F0-9]{40}$', address):
            return True, "Ethereum"
    
    # TON addresses
    ton_pattern = r'^(UQ|EQ|kQ|0Q)[A-Za-z0-9_-]{46}$'
    if re.match(ton_pattern, address):
        return True, "TON"
    # Raw TON format
    raw_pattern = r'^-?[0-9]+:[a-fA-F0-9]{64}$'
    if re.match(raw_pattern, address):
        return True, "TON"
    
    # Solana addresses (base58, 32-44 chars, no 0, O, I, l)
    if 32 <= len(address) <= 44:
        if not re.search(r'[0OIl]', address) and re.match(r'^[1-9A-HJ-NP-Za-km-z]+$', address):
            return True, "Solana"
    
    # Monero addresses (95 or 106 chars, starts with 4)
    if address.startswith('4'):
        if len(address) == 95 or len(address) == 106:
            if re.match(r'^4[0-9A-Za-z]{94,105}$', address):
                return True, "Monero"
    
    # USDT/USDC on Ethereum (same format as Ethereum)
    if address.startswith('0x') and len(address) == 42:
        if re.match(r'^0x[a-fA-F0-9]{40}$', address):
            return True, "USDT"  # Default to USDT for ERC-20
    
    return False, "Unknown"


def detect_coin_from_address(address):
    """Detect cryptocurrency type from address format"""
    is_valid, coin_name = is_valid_crypto_address(address)
    return coin_name


def is_valid_ton_address(address):
    if not address:
        return False
    ton_pattern = r'^(UQ|EQ|kQ|0Q)[A-Za-z0-9_-]{46}$'
    if re.match(ton_pattern, address):
        return True
    raw_pattern = r'^-?[0-9]+:[a-fA-F0-9]{64}$'
    if re.match(raw_pattern, address):
        return True
    return len(address) >= 48 and len(address) <= 67
