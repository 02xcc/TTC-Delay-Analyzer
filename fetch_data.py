"""Download TTC Open Data resources, without writing to the database."""
import requests

API_URL = "https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_show"
PACKAGE_ID = "ttc-subway-delay-data"
TIMEOUT = 60


def list_resources(session=requests):
    response = session.get(API_URL, params={"id": PACKAGE_ID}, timeout=TIMEOUT)
    response.raise_for_status()
    result = response.json()
    if not result.get("success") or "resources" not in result.get("result", {}):
        raise ValueError("Toronto Open Data returned an unexpected package response")
    return result["result"]["resources"]


def find_resource(resources, kind, formats=("CSV", "JSON")):
    """Match the right named resource rather than the first CSV in the dataset."""
    if kind == "delays":
        marker = "ttc subway delay data since 2025"
    elif kind == "codes":
        marker = "code descriptions"
    else:
        raise ValueError("kind must be 'delays' or 'codes'")

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
    for entry in list_resources():
        print(entry.get("name"), entry.get("format"), entry.get("url"))
