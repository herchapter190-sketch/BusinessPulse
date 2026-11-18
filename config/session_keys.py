"""Names used to keep data in the browser session (memory only), shared by pages."""

ACTIVE_FILE = "active_file"          # {"name", "content", "source"} of the loaded file
LOADED_DATA = "loaded_data"          # tools.file_loader.LoadedData for the chosen sheet
SELECTED_SHEET = "selected_sheet"
UPLOADER_VERSION = "uploader_version"
CLEANING_SESSION = "cleaning_session"  # tools.data_cleaning.CleaningSession (original / working copy)
CLEANED_DATA = "cleaned_data"          # tools.data_cleaning.CleanedData, approved by the user
CLEANING_PLAN = "cleaning_fix_plan"    # (key, tools.data_cleaning.FixPlan) for the current working copy
ROLE_OVERRIDES = "role_overrides"      # {dataset signature: {role: column or None}} chosen on Data quality

# A second, separate file used by the dashboard (e.g. expenses next to sales).
# {"name", "content", "source", "sheet"}; it is never merged row-by-row with the main file.
COMPANION_FILE = "companion_file"

# The choices shared by the Analysis pages (data version, counting options, dates,
# product/category/region filters), so they carry over when moving between pages.
# {"version", "pending", "errors", "dates", "product", "category", "region"}; see ui/analysis_context.py.
ANALYSIS_SHARED = "analysis_shared"

# Everything that belongs to one loaded file; cleared when the file is removed or replaced.
FILE_SCOPED = (ACTIVE_FILE, LOADED_DATA, SELECTED_SHEET, CLEANING_SESSION, CLEANED_DATA, CLEANING_PLAN,
               ROLE_OVERRIDES, ANALYSIS_SHARED)
