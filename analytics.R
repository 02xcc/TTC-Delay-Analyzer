library(DBI)
library(RSQLite)
library(dplyr)

con <- DBI::dbConnect(RSQLite::SQLite(), dbname = "local_ttc.db")
ttc_data <- dbReadTable(con, "delays")

route_summary <- ttc_data %>%
  filter(!is.na(Min.Delay) & Min.Delay > 0) %>%
  group_by(Line) %>%
  summarize(
    mean_delay = round(mean(Min.Delay, na.rm = TRUE), 2),
    max_delay = max(Min.Delay, na.rm = TRUE),
    total_incidents = n()
  ) %>%
  arrange(desc(mean_delay))

dbWriteTable(con, "route_summary", route_summary, overwrite = TRUE)
print("Success: route_summary table created!")
dbDisconnect(con)

