"""Static configuration: window, incident events, pipeline DAG, simulation parameters."""

from datetime import date, datetime, timedelta

# --- Window -----------------------------------------------------------------
WINDOW_END = date(2026, 10, 19)  # incident Monday
WINDOW_DAYS = 60
WINDOW_START = WINDOW_END - timedelta(days=WINDOW_DAYS - 1)
INCIDENT_DATE = WINDOW_END
# Claims are simulated from here so the open-claim inventory is at steady state
# by the time the window starts.
BURN_IN_START = date(2026, 1, 1)

# America/Chicago is CDT (UTC-5) for the whole window; DST ends 2026-11-01.
# Burn-in timestamps (CST) are never written out.
UTC_OFFSET = "-05:00"

# --- Incident and red-herring events -----------------------------------------
PAS_RELEASE_AT = datetime(2026, 10, 18, 22, 0)  # PAS 4.12: policy_status -> pol_status_cd
DBT_DEPLOY_AT = datetime(2026, 10, 16, 14, 0)   # unrelated dbt deploy
DBT_DEPLOY_MODEL = "fct_fnol"                   # changed model; full refresh on next run
HR_FEED_DELAY_DATE = INCIDENT_DATE
HR_FEED_DELAY_MIN = 50

# --- Entities -----------------------------------------------------------------
POLICY_STATUSES = ("ACTIVE", "LAPSED", "CANCELLED")
POLICY_STATUS_MIX = (0.965, 0.022, 0.013)
N_POLICIES_START = 150_000
NEW_POLICIES_PER_DAY = 60

# name, policy mix, severity median ($), severity sigma, median days to close
LOBS = (
    ("Personal Auto", 0.45, 4_200, 0.9, 18),
    ("Homeowners", 0.30, 9_500, 1.0, 30),
    ("Commercial Property", 0.12, 22_000, 1.1, 45),
    ("Commercial Auto", 0.08, 11_000, 1.0, 35),
    ("General Liability", 0.05, 18_000, 1.2, 60),
)
CLOSE_SIGMA = 0.9
WEEKEND_CLOSE_DEFER = 0.85  # share of weekend closures that slip to Monday

FNOL_PER_DAY = 240
FNOL_DOW_FACTOR = (1.15, 1.08, 1.03, 1.00, 0.97, 0.62, 0.55)  # Mon..Sun

# Loss-history write-back: adjuster closures post to the policy in real time;
# TPA closures post in the weekly PAS batch (Sunday 23:00, in the maintenance
# window). Which channel a claim uses is independent of LOB and severity.
TPA_CLOSE_SHARE = 0.35
TPA_BATCH_DOW = 6  # Sunday
TPA_BATCH_AT_H = 23

ENDORSEMENTS_WEEKDAY = 1_800
ENDORSEMENTS_WEEKEND = 400
NIGHTLY_BATCH_POLICIES = 900  # PAS billing/renewal batch touches at 00:30
NIGHTLY_BATCH_AT_H = 0.5

CLAIM_UPDATE_RATE = 0.12  # daily reserve/payment updates per open claim (weekday)
CLAIM_UPDATE_WEEKEND_FACTOR = 0.3
ADJUSTER_HEADCOUNT_START = 212

# --- Pipeline -----------------------------------------------------------------
# Trailing window rebuilt by fct_claims_closed / int_claims_enriched.
CLOSED_WINDOW_DAYS = 7
INGEST_AT = "02:00"
HR_FILE_EXPECTED_AT = "03:00"
HR_FILE_ARRIVAL_SD_MIN = 4

WAREHOUSE_CREDITS_PER_HOUR = {"LOADING_WH": 1, "TRANSFORM_WH": 2, "BI_WH": 1}
MIN_BILLED_S = 60
QUEUE_WAIT_MEAN_S = 20
DURATION_NOISE_SIGMA = 0.12
P_TRANSIENT_FAIL = 0.012
RETRY_BACKOFF_S = 300

# Topological order. `scans` lists the (table, field) row counts the job reads,
# taken from row_volume of the same run. `writes` is the table it produces.
JOBS = {
    "raw_policies": dict(
        layer="ingest", upstream=(), schedule="02:00", sla="03:00", warehouse="LOADING_WH",
        writes="raw_policies", scans=(("raw_policies", "rows_written"),),
        base_s=240, scan_s_per_k=0.12, write_s_per_k=0.05),
    "raw_claims": dict(
        layer="ingest", upstream=(), schedule="02:00", sla="03:00", warehouse="LOADING_WH",
        writes="raw_claims", scans=(("raw_claims", "rows_written"),),
        base_s=180, scan_s_per_k=0.12, write_s_per_k=0.05),
    "raw_adjuster_hr": dict(
        layer="ingest", upstream=(), schedule="02:45", sla="03:30", warehouse="LOADING_WH",
        writes="raw_adjuster_hr", scans=(("raw_adjuster_hr", "rows_written"),),
        base_s=60, scan_s_per_k=0.5, write_s_per_k=0.5),
    "stg_policies": dict(
        layer="staging", upstream=("raw_policies",), schedule="04:00", sla="05:30",
        warehouse="TRANSFORM_WH", writes="stg_policies",
        scans=(("raw_policies", "table_row_count"),),
        base_s=45, scan_s_per_k=0.25, write_s_per_k=0.05),
    "stg_claims": dict(
        layer="staging", upstream=("raw_claims",), schedule="04:00", sla="05:30",
        warehouse="TRANSFORM_WH", writes="stg_claims",
        scans=(("raw_claims", "table_row_count"),),
        base_s=40, scan_s_per_k=0.25, write_s_per_k=0.05),
    "int_claims_enriched": dict(
        layer="intermediate", upstream=("stg_policies", "stg_claims"), schedule="04:00",
        sla="05:30", warehouse="TRANSFORM_WH", writes="int_claims_enriched",
        scans=(("stg_policies", "table_row_count"), ("stg_claims", "table_row_count")),
        base_s=60, scan_s_per_k=0.4, write_s_per_k=0.3),
    "fct_claims_closed": dict(
        layer="mart", upstream=("int_claims_enriched",), schedule="04:00", sla="05:30",
        warehouse="TRANSFORM_WH", writes="fct_claims_closed",
        scans=(("int_claims_enriched", "table_row_count"),),
        base_s=30, scan_s_per_k=0.3, write_s_per_k=0.3),
    "fct_claims_open": dict(
        layer="mart", upstream=("int_claims_enriched",), schedule="04:00", sla="05:30",
        warehouse="TRANSFORM_WH", writes="fct_claims_open",
        scans=(("int_claims_enriched", "table_row_count"),),
        base_s=30, scan_s_per_k=0.3, write_s_per_k=0.3),
    "fct_fnol": dict(
        layer="mart", upstream=("stg_claims",), schedule="04:00", sla="05:30",
        warehouse="TRANSFORM_WH", writes="fct_fnol",
        scans=(("stg_claims", "table_row_count"),),
        base_s=25, scan_s_per_k=0.2, write_s_per_k=1.5),
    "dash_claims_severity": dict(
        layer="dashboard", upstream=("fct_claims_closed", "fct_claims_open", "fct_fnol"),
        schedule="05:45", sla="06:30", warehouse="BI_WH", writes=None,
        scans=(("fct_claims_closed", "rows_written"), ("fct_claims_open", "rows_written"),
               ("fct_fnol", "rows_written")),
        base_s=90, scan_s_per_k=0.5, write_s_per_k=0.0),
    "dash_adjuster_staffing": dict(
        layer="dashboard", upstream=("raw_adjuster_hr",), schedule="05:45", sla="06:30",
        warehouse="BI_WH", writes=None, scans=(("raw_adjuster_hr", "table_row_count"),),
        base_s=45, scan_s_per_k=0.5, write_s_per_k=0.0),
}
