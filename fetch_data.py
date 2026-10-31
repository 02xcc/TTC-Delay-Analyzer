import requests

base_url = "https://ckan0.cf.opendata.inter.prod-toronto.ca"
url = base_url + "/api/3/action/package_show"

params = {"id": "ttc-subway-delay-data"}

response = requests.get(url, params=params, timeout=30)
response.raise_for_status()

package = response.json()

resources = package["result"]["resources"]

for resource in resources:
    if resource["name"] == "Code Descriptions.json":
        resource_url = resource["url"]
        print(resource_url)

information = requests.get(resource_url)
information.raise_for_status()
data = information.json()
print(type(data))
print(data)