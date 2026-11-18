"""Stage 6.1: the shared data context used by every Analysis page (ui/analysis_context.py)."""

from streamlit.testing.v1 import AppTest

from config import session_keys
from tools import data_cleaning as dc
from tools.file_loader import dataset_signature, load_file
from tools.sample_data import read_sample

TIMEOUT = 120


def _context_page():
    import streamlit as st

    from ui import analysis_context as ctx

    loaded, signature = ctx.loaded_file()
    if loaded is None:
        st.write("nothing loaded")
        st.stop()
    st.write("versions: " + " | ".join(ctx.main_versions(loaded, signature)))
    data = ctx.select_data(loaded, signature, include_errors=False)
    st.write(f"chosen: {data.version}")
    st.write(f"sales: {data.sales.name if data.sales else None}")
    st.write(f"expenses: {data.expenses.name if data.expenses else None}")
    st.write(f"companion error: {data.companion_error}")
    st.write(f"currency: {data.currency_text}")


def run(main=None, companion=None, session=None, approve=True) -> dict[str, str]:
    app = AppTest.from_function(_context_page, default_timeout=TIMEOUT)
    if main:
        name, content = read_sample(main)
        app.session_state[session_keys.ACTIVE_FILE] = {"name": name, "content": content, "source": "sample"}
        app.session_state[session_keys.LOADED_DATA] = load_file(name, content)
    if companion:
        name, content = read_sample(companion)
        app.session_state[session_keys.COMPANION_FILE] = {"name": name, "content": content, "source": "sample",
                                                          "sheet": None}
    if session is not None:
        app.session_state[session_keys.CLEANING_SESSION] = session
        if approve:
            app.session_state[session_keys.CLEANED_DATA] = session.approve()
    app.run()
    assert not app.exception, app.exception
    lines = [block.value for block in app.markdown]
    return dict(line.split(": ", 1) for line in lines if ": " in line)


def messy_session():
    name, content = read_sample("sales_messy")
    session = dc.CleaningSession(load_file(name, content).data, dataset_signature(name, None, content), name)
    session.apply(dc.propose_exclude_duplicates(session), confirmed=True)
    return session


def test_nothing_loaded():
    app = AppTest.from_function(_context_page, default_timeout=TIMEOUT)
    app.run()
    assert not app.exception and app.markdown[0].value == "nothing loaded"


def test_sales_with_separate_expenses():
    info = run("sales_clean", "expenses_clean")
    assert info["versions"] == "Original upload"
    assert (info["sales"], info["expenses"]) == ("sales_clean.csv", "expenses_clean.csv")
    assert info["companion error"] == "None" and info["currency"] == "Not specified"


def test_expense_file_as_main_file():
    info = run("expenses_clean", "sales_clean")
    assert (info["sales"], info["expenses"]) == ("sales_clean.csv", "expenses_clean.csv")


def test_second_file_of_the_same_kind_is_not_used():
    info = run("sales_clean", "sales_messy")
    assert info["sales"] == "sales_clean.csv" and info["expenses"] == "None"
    assert "Only the main file is used" in info["companion error"]


def test_cleaned_version_is_preferred_then_working_copy_then_original():
    approved = run("sales_messy", session=messy_session())
    assert approved["versions"] == "Cleaned dataset | Original upload"
    assert approved["chosen"] == "Cleaned dataset"

    working = run("sales_messy", session=messy_session(), approve=False)
    assert working["versions"] == "Working copy (not approved) | Original upload"
    assert working["chosen"] == "Working copy (not approved)"
