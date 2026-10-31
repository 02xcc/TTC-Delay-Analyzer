import io
import pandas as pd
import requests
from db import engine

def get_csv_url():
    api_url = "https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_show"
    params = {"id": "ttc-subway-delay-data"}
    response = requests.get(api_url, params=params)
    
    if response.status_code == 200:
        resources = response.json()["result"]["resources"]
        for res in resources:
            if res["format"].lower() == "csv":
                return res["url"]
    return None

def get_data(url):
    print(f"Downloading CSV from: {url}")
    response = requests.get(url)
    if response.status_code == 200:
        csv_data = io.StringIO(response.text)
        return pd.read_csv(csv_data)
    else:
        print(f"Failed to retrieve data. Status Code: {response.status_code}")
        return None

if __name__ == "__main__":
    print("Locating latest TTC data URL...")
    csv_url = get_csv_url()
    
    if csv_url:
        df = get_data(csv_url)
        if df is not None:
            print(f"Loaded {len(df)} rows. Writing to the database...")
            df.to_sql("delays", engine, if_exists="replace", index=False)
            print("Success! Data is now in the 'delays' table.")
    else:
        print("Could not find the CSV URL from the Open Data API.")


