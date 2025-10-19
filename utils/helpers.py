"""Helper utility functions."""

from typing import Optional
from datetime import datetime


def normalize_symbol(symbol: str, exchange: str = 'generic') -> str:
    """Normalize symbol names across exchanges.

    Args:
        symbol: Original symbol name
        exchange: Exchange name (bybit, coindcx, etc.)

    Returns:
        Normalized base coin (e.g., BTC, ETH)
    """
    # Remove exchange-specific prefixes
    symbol = symbol.replace('B-', '')  # CoinDCX prefix

    # Extract base coin
    if '_' in symbol:
        base_coin = symbol.split('_')[0]
    elif 'USDT' in symbol:
        base_coin = symbol.replace('USDT', '')
    elif 'USD' in symbol:
        base_coin = symbol.replace('USD', '')
    else:
        base_coin = symbol

    return base_coin.upper()


def format_price(price: float, decimals: int = 2) -> str:
    """Format price with specified decimal places.

    Args:
        price: Price value
        decimals: Number of decimal places

    Returns:
        Formatted price string
    """
    return f"${price:,.{decimals}f}"


def format_percentage(value: float, decimals: int = 4) -> str:
    """Format percentage value.

    Args:
        value: Decimal value (e.g., 0.0001 for 0.01%)
        decimals: Number of decimal places

    Returns:
        Formatted percentage string
    """
    return f"{value * 100:.{decimals}f}%"


def parse_timestamp(timestamp_str: str) -> Optional[datetime]:
    """Parse ISO format timestamp string.

    Args:
        timestamp_str: ISO format timestamp

    Returns:
        datetime object or None if parsing fails
    """
    try:
        # Remove 'Z' and parse
        if timestamp_str.endswith('Z'):
            timestamp_str = timestamp_str[:-1] + '+00:00'
        return datetime.fromisoformat(timestamp_str)
    except Exception:
        return None


def is_data_fresh(timestamp_str: str, max_age_seconds: int = 60) -> bool:
    """Check if data is fresh based on timestamp.

    Args:
        timestamp_str: ISO format timestamp
        max_age_seconds: Maximum age in seconds

    Returns:
        True if data is fresh, False otherwise
    """
    timestamp = parse_timestamp(timestamp_str)
    if not timestamp:
        return False

    age = (datetime.utcnow() - timestamp.replace(tzinfo=None)).total_seconds()
    return age <= max_age_seconds
