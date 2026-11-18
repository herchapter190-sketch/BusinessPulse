"""Application settings for BusinessPulse.

Keep MAX_UPLOAD_MB in step with `server.maxUploadSize` in .streamlit/config.toml
(a test checks that they match).
"""

APP_NAME = "BusinessPulse"

# File upload
SUPPORTED_EXTENSIONS = (".csv", ".xlsx")
MAX_UPLOAD_MB = 50

# Preview
PREVIEW_ROWS = 100

# Column type detection looks at no more than this many filled values per column,
# so very large files stay fast. Counts of filled/empty cells always use every row.
TYPE_DETECTION_SAMPLE_SIZE = 50_000
