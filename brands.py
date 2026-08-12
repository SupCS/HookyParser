"""Public cinema brand and location configuration.

Every brand is a separate collection namespace.  Site and circuit identifiers are
public values used by the INDY consumer storefronts.
"""

BRANDS = {
    "hooky": {
        "name": "Hooky Entertainment",
        "locations": {
            "addison": {"name": "Addison", "base_url": "https://hookyentertainment.com/addison", "site_id": 217, "circuit_id": 119, "timezone": "America/Chicago"},
            "baytown": {"name": "Baytown", "base_url": "https://hookyentertainment.com/baytown", "site_id": 216, "circuit_id": 119, "timezone": "America/Chicago"},
            "cary": {"name": "Cary", "base_url": "https://hookyentertainment.com/cary", "site_id": 221, "circuit_id": 119, "timezone": "America/New_York"},
            "delray": {"name": "Delray Beach", "base_url": "https://hookyentertainment.com/delray", "site_id": 222, "circuit_id": 119, "timezone": "America/New_York"},
            "fredericksburg": {"name": "Fredericksburg", "base_url": "https://hookyentertainment.com/fredericksburg", "site_id": 220, "circuit_id": 119, "timezone": "America/New_York"},
            "homestead": {"name": "Homestead", "base_url": "https://hookyentertainment.com/homestead", "site_id": 223, "circuit_id": 119, "timezone": "America/New_York"},
            "hutto": {"name": "Hutto", "base_url": "https://hookyentertainment.com/hutto", "site_id": 214, "circuit_id": 119, "timezone": "America/Chicago"},
            "nashville": {"name": "Nashville", "base_url": "https://hookyentertainment.com/nashville", "site_id": 224, "circuit_id": 119, "timezone": "America/Chicago"},
            "southlake": {"name": "Southlake", "base_url": "https://hookyentertainment.com/southlake", "site_id": 206, "circuit_id": 119, "timezone": "America/Chicago"},
            "waxahachie": {"name": "Waxahachie", "base_url": "https://hookyentertainment.com/waxahachie", "site_id": 218, "circuit_id": 119, "timezone": "America/Chicago"},
        },
    },
    "showbiz": {
        "name": "ShowBiz Cinemas",
        "locations": {
            "edmond": {"name": "Edmond", "base_url": "https://edmond.showbizcinemas.com", "site_id": 225, "circuit_id": 119, "timezone": "America/Chicago"},
            "fallcreek": {"name": "Fall Creek", "base_url": "https://fallcreek.showbizcinemas.com", "site_id": 226, "circuit_id": 119, "timezone": "America/Chicago"},
            "kingwood": {"name": "Kingwood", "base_url": "https://kingwood.showbizcinemas.com", "site_id": 227, "circuit_id": 119, "timezone": "America/Chicago"},
            "libertylakes": {"name": "Liberty Lakes", "base_url": "https://libertylakes.showbizcinemas.com", "site_id": 228, "circuit_id": 119, "timezone": "America/Chicago"},
        },
    },
    "violet-crown": {
        "name": "Violet Crown",
        "locations": {
            "austin": {"name": "Austin", "base_url": "https://austin.violetcrown.com", "site_id": 127, "circuit_id": 84, "timezone": "America/Chicago"},
            "charlottesville": {"name": "Charlottesville", "base_url": "https://charlottesville.violetcrown.com", "site_id": 129, "circuit_id": 84, "timezone": "America/New_York"},
            "dallas": {"name": "Dallas", "base_url": "https://dallas.violetcrown.com", "site_id": 211, "circuit_id": 84, "timezone": "America/Chicago"},
        },
    },
    "times-square": {
        "name": "Times Square Grand Slam",
        "locations": {
            "tyler": {"name": "Tyler", "base_url": "https://tylermovies.com", "site_id": 219, "circuit_id": 119, "timezone": "America/Chicago"},
        },
    },
}

DEFAULT_BRAND = "hooky"


def public_brand_config() -> dict:
    """Return browser-safe brand metadata."""
    return {
        slug: {
            "name": brand["name"],
            "locations": {
                location: {"name": details["name"], "timezone": details["timezone"]}
                for location, details in brand["locations"].items()
            },
        }
        for slug, brand in BRANDS.items()
    }
