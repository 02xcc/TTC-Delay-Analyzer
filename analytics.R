# Optional manual R analytics. The Python pipeline also updates this SQL table
# automatically during monthly imports; this script can recompute it on demand.
library(DBI)
library(RPostgres)
library(dplyr)

url <- Sys.getenv("DATABASE_URL")
if (!nzchar(url)) stop("Set DATABASE_URL before running analytics.R")

# RPostgres does NOT parse a full PostgreSQL URL via `dbname`.
# Extract the components from the same Neon/local DATABASE_URL used in Python.
parts <- regmatches(
  url,
  regexec("^postgres(?:ql)?://([^@]+)@([^:/?]+)(?::([0-9]+))?/([^?]+)(?:\\?(.*))?$",
          url, perl = TRUE)
)[[1]]
if (length(parts) != 6) stop("DATABASE_URL must be a PostgreSQL URL with user, password, host and dbname")
userinfo <- parts[2]
if (!grepl(":", userinfo, fixed = TRUE)) stop("DATABASE_URL is missing a password")
username <- utils::URLdecode(sub(":.*$", "", userinfo))
password <- utils::URLdecode(sub("^[^:]+:", "", userinfo))
host <- parts[3]
port <- if (nzchar(parts[4])) as.integer(parts[4]) else 5432L
database <- utils::URLdecode(parts[5])
query <- parts[6]
sslmode <- if (grepl("(^|&)sslmode=require(&|$)", query)) "require" else "prefer"
args <- list(drv = RPostgres::Postgres(), host = host, port = port,
             dbname = database, user = username, password = password,
             sslmode = sslmode)
if (grepl("(^|&)channel_binding=require(&|$)", query)) {
  args$channel_binding <- "require"
}
con <- do.call(DBI::dbConnect, args)

# Read directly from the existing PostgreSQL table, not local_ttc.db.
ttc_data <- DBI::dbReadTable(con, "subway_delays")
route_summary <- ttc_data %>%
  filter(!is.na(min_delay) & min_delay > 0, !is.na(line)) %>%
  group_by(line) %>%
  summarize(
    mean_delay = round(mean(min_delay), 2),
    max_delay = max(min_delay),
    total_incidents = n(),
    .groups = "drop"
  ) %>%
  arrange(desc(mean_delay))

DBI::dbWithTransaction(con, {
  DBI::dbExecute(con, "DELETE FROM route_summary")
  DBI::dbWriteTable(con, "route_summary", route_summary, append = TRUE,
                    row.names = FALSE)
})
cat("Success: route_summary recalculated from PostgreSQL!\n")

DBI::dbDisconnect(con)
