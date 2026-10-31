import pandas as pd
import requests
from sqlalchemy import create_engine
import io

def get_data(url): 
	response = requests.get(url)
	if response.status_code ==20: 
		csv_data = io.StringIO(response.text)
		df = pd.read_csv(csv_data)
		return df
	
##	else:
##		print(f"Failed to retrieve the data. Status Code: {response.status_code})
##		return none;
	


