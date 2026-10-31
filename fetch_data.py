"""Download TTC Open Data resources, without writing to the database."""
import requests

API_URL = "https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_show"
TIMEOUT = 60

def list_resources(package_id="ttc-subway-delay-data", session=requests):
    """Fetch resources for a specific CKAN package ID."""
    response = session.get(API_URL, params={"id": package_id}, timeout=TIMEOUT)
    response.raise_for_status()
    result = response.json()
    if not result.get("success") or "resources" not in result.get("result", {}):
        raise ValueError(f"Toronto Open Data returned an unexpected response for {package_id}")
    return result["result"]["resources"]

def find_resource(resources, kind, formats=("CSV", "JSON")):
    """Match the right named resource based on mode."""
    if kind == "subway_delays":
        marker = "ttc subway delay data since 2025"
    elif kind == "bus_delays":
        marker = "ttc bus delay data since 2025"
    elif kind == "streetcar_delays":
        marker = "ttc streetcar delay data since 2025"
    elif kind == "codes":
        marker = "code descriptions"
    else:
        raise ValueError("kind must be 'subway_delays', 'bus_delays', 'streetcar_delays', or 'codes'")

    for fmt in formats:
        for resource in resources:
            name = str(resource.get("name", "")).lower()
            actual_format = str(resource.get("format", "")).upper()
            if marker in name and actual_format == fmt and resource.get("url"):
                return resource
    raise ValueError(
        f"Could not find the {kind} resource as {', '.join(formats)}. "
        "Check the resource list returned by the TTC API."
    )

def download_resource(resource, session=requests):
    response = session.get(resource["url"], timeout=120)
    response.raise_for_status()
    return response

if __name__ == "__main__":
    # Test discovery for all three modes and print metadata inventory
    for pkg in ["ttc-subway-delay-data", "ttc-bus-delay-data", "ttc-streetcar-delay-data"]:
        print(f"\n--- PACKAGE: {pkg} ---")
        try:
            for entry in list_resources(pkg):
                print(f"{entry.get('name')} | {entry.get('format')} | {entry.get('last_modified')} | {entry.get('url')}")
        except Exception as e:
            print(f"Failed to fetch {pkg}: {e}")
