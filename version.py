"""
Version management for Crypto Price LTP system
"""

VERSION = "1.0.0"
BUILD_DATE = "2024-11-24"

def get_version():
    """Get the current version of the system"""
    return VERSION

def get_version_info():
    """Get detailed version information"""
    return {
        "version": VERSION,
        "build_date": BUILD_DATE,
        "application": "Crypto Price LTP Monitor"
    }