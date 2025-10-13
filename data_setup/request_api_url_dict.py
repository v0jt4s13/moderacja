"""
Configuration of external vendor APIs used by the price_compare feature.

Each entry defines an API endpoint transporting product data together with a
public page URL that will be presented to the user.
"""

REQUEST_API_URLS = {
    "Castorama_Logistics_Portal": {
        "main_url": "https://www.castorama.pl/",
        "urls": {
            "api_url": "https://castorama.shippeo.com/",
            "url": "https://www.castorama.pl/marketplace"
        }
    },
    "LeroyMerlin_Developer_Portal": {
        "main_url": "https://www.leroymerlin.pl/",
        "urls": {
            "api_url": "https://developer.leroymerlin.fr/",
            "url": "https://www.leroymerlin.pl/mapa-produktu.html"
        }
    },
    "LeroyMerlin_Supplier_Portal": {
        "main_url": "https://www.leroymerlin.pl/",
        "urls": {
            "api_url": "https://supplier.merlinsourcing.com/",
            "url": "https://www.leroymerlin.pl/"
        }
    },
    "OBI_B2B_ServicePartnership": {
        "main_url": "https://www.obi.pl/",
        "urls": {
            "api_url": "https://www.obi.pl/info/firmy-uslugowe",
            "url": "https://www.obi.pl/"
        }
    },
    "B_plus_B_InvestmentService": {
        "main_url": "https://bplusb.pl/",
        "urls": {
            "api_url": "https://bplusb.pl/obsluga-inwestycji",
            "url": "https://bplusb.pl/"
        }
    },
    "PSB_Group_PartnerPortal": {
        "main_url": "https://www.grupapsb.com.pl/",
        "urls": {
            "api_url": "https://portalpartnera.grupapsb.com.pl/",
            "page_url": "https://www.grupapsb.com.pl/"
        }
    },
}


def get_vendor_sources() -> dict[str, dict[str, object]]:
    """Return a normalized copy of vendor descriptors.

    The new configuration groups URLs under the ``urls`` key and provides a
    ``main_url`` anchor.  This helper flattens the frequently used fields so
    templates and view code can continue to rely on ``api_url`` and
    ``page_url`` while still exposing the raw ``urls`` block for future use.
    """
    normalized: dict[str, dict[str, object]] = {}
    for vendor_name, meta in REQUEST_API_URLS.items():
        urls = dict(meta.get("urls") or {})
        api_url = urls.get("api_url")
        page_url = urls.get("page_url") or urls.get("url") or meta.get("main_url")

        normalized[vendor_name] = {
            "main_url": meta.get("main_url"),
            "api_url": api_url,
            "page_url": page_url,
            "urls": urls,
        }
    return normalized
