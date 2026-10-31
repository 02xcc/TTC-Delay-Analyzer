library(DBI)
library(RPostgres)
library(dplyr)

# 2. Connect to the local PostgreSQL database
con <- DBI::dbConnect(
  RPostgres::Postgres(),
  dbname = "ttc_project",
  host = "localhost",
  port = 5432,
  user = "postgres",
  password = "" # Leave blank, or enter your database password if you configured one
)

# 3. Read the raw TTC data from the database
ttc_data <- dbReadTable(con, "delays")

# 4. Clean and Analyze the data using dplyr
route_summary <- ttc_data %>%
  # Clean: Use backticks around `Min Delay` because of the space in the column name
  filter(!is.na(`Min Delay`) & `Min Delay` > 0) %>%
  
  # Analyze: Group the data by subway Line instead of Route
  group_by(Line) %>%
  
  # Analyze: Calculate the historical statistics using the correct delay column
  summarize(
    mean_delay = mean(`Min Delay`, na.rm = TRUE),
    max_delay = max(`Min Delay`, na.rm = TRUE),
    total_incidents = n()
  ) %>%
  # Sort the data to put the worst lines at the top
  arrange(desc(mean_delay))


# 5. Visually inspect the results in RStudio
View(route_summary)

# 6. Save the summary table back to PostgreSQL for your Flask web app
dbWriteTable(con, "route_summary", route_summary, overwrite = TRUE)

# 7. Close the database connection
dbDisconnect(con)

