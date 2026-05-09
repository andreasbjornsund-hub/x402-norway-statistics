"""Currencies tracked by Norges Bank for which exchange rates are available.

Source: https://www.norges-bank.no/en/topics/Statistics/exchange_rates/
ISO 4217 alpha-3 codes. NOK is the quote currency for all rates.
"""

CURRENCIES: list[dict] = [
    {"code": "USD", "name": "US Dollar"},
    {"code": "EUR", "name": "Euro"},
    {"code": "GBP", "name": "British Pound Sterling"},
    {"code": "SEK", "name": "Swedish Krona"},
    {"code": "DKK", "name": "Danish Krone"},
    {"code": "ISK", "name": "Icelandic Krona"},
    {"code": "CHF", "name": "Swiss Franc"},
    {"code": "JPY", "name": "Japanese Yen"},
    {"code": "CNY", "name": "Chinese Yuan Renminbi"},
    {"code": "AUD", "name": "Australian Dollar"},
    {"code": "CAD", "name": "Canadian Dollar"},
    {"code": "NZD", "name": "New Zealand Dollar"},
    {"code": "HKD", "name": "Hong Kong Dollar"},
    {"code": "SGD", "name": "Singapore Dollar"},
    {"code": "KRW", "name": "South Korean Won"},
    {"code": "INR", "name": "Indian Rupee"},
    {"code": "BRL", "name": "Brazilian Real"},
    {"code": "MXN", "name": "Mexican Peso"},
    {"code": "ZAR", "name": "South African Rand"},
    {"code": "TRY", "name": "Turkish Lira"},
    {"code": "RUB", "name": "Russian Ruble"},
    {"code": "PLN", "name": "Polish Zloty"},
    {"code": "CZK", "name": "Czech Koruna"},
    {"code": "HUF", "name": "Hungarian Forint"},
    {"code": "BGN", "name": "Bulgarian Lev"},
    {"code": "RON", "name": "Romanian Leu"},
    {"code": "ILS", "name": "Israeli Shekel"},
    {"code": "IDR", "name": "Indonesian Rupiah"},
    {"code": "MYR", "name": "Malaysian Ringgit"},
    {"code": "PHP", "name": "Philippine Peso"},
    {"code": "THB", "name": "Thai Baht"},
    {"code": "TWD", "name": "Taiwan Dollar"},
    {"code": "VND", "name": "Vietnamese Dong"},
    {"code": "PKR", "name": "Pakistani Rupee"},
    {"code": "XDR", "name": "Special Drawing Rights"},
    {"code": "CLP", "name": "Chilean Peso"},
    {"code": "COP", "name": "Colombian Peso"},
    {"code": "EGP", "name": "Egyptian Pound"},
    {"code": "MAD", "name": "Moroccan Dirham"},
    {"code": "AED", "name": "UAE Dirham"},
    {"code": "SAR", "name": "Saudi Riyal"},
    # NOK is the quote currency, not a tradable pair from this API.
    {"code": "NOK", "name": "Norwegian Krone"},
]

CODES = {c["code"] for c in CURRENCIES}


def is_supported(code: str) -> bool:
    return (code or "").upper() in CODES
