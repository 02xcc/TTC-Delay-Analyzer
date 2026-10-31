from flask import Flask, render_template
import pandas as pd
from sqlalchemy import inspect
from db import engine

app = Flask(__name__)

@app.route("/")
def home():
    inspector = inspect(engine)
    if "route_summary" not in inspector.get_table_names():
        return "<h1>Waiting on Analytics</h1><p>Student B needs to run analytics.R!</p>"
    
    query = "SELECT * FROM route_summary ORDER BY mean_delay DESC;"
    df = pd.read_sql(query, engine)
    data = df.to_dict(orient="records")
    
    return render_template("home.html", routes=data)

@app.route("/search")
def search():
    return render_template("search.html")

@app.route("/story")
def story():
    return render_template("story.html")

if __name__ == "__main__":
    app.run(debug=True, port=5000)

    