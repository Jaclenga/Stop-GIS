import streamlit as st

def render_methodology_page() -> None:
    st.title("Project Documentation")
    methodology = st.session_state["methodology"]

    st.caption("Edit the published methodology here. Use Preview → Methodology for the authoritative public rendering.")
    edit = st.container()
    with edit:
        methodology["title"] = st.text_input("About page title", methodology["title"])
        methodology["summary"] = st.text_area("Summary", methodology["summary"], height=85)
        methodology["purpose"] = st.text_area("Rationale", methodology["purpose"], height=130)
        methodology["shade_method"] = st.text_area("Shade assessment method", methodology["shade_method"], height=130)
        methodology["data_sources"] = st.text_area(
            "Data sources",
            methodology["data_sources"],
            height=135,
            placeholder=(
                "- GTFS stops and routes\n"
                "- Manually reviewed example shade datapoints\n"
                "- Imagery source used for waiting-area shade review\n"
                "- Optional project-specific attributes and GIS overlays"
            ),
        )
        methodology["contributors"] = st.text_area("Contributors", methodology["contributors"], height=85)
        methodology["limitations"] = st.text_area("Known limitations", methodology["limitations"], height=110)
        methodology.setdefault("bibliography", "")
        methodology["bibliography"] = st.text_area(
            "Bibliography",
            methodology["bibliography"],
            height=170,
            help=(
                "Use the same grouped APA format as citations: unindented lines are group labels, "
                "and indented lines render as hanging-indent bibliography entries."
            ),
            placeholder=(
                "Works referenced:\n"
                "    Google. (n.d.). Google Maps imagery [Map and street-level imagery]. Retrieved Month Day, Year, from https://www.google.com/maps\n"
                "    Transit Agency. (Year). General Transit Feed Specification (GTFS) data feed [Data set]. URL\n"
                "    Author, A. A., & Author, B. B. (Year). Title of article. Title of Journal, volume(issue), page range. https://doi.org/xxxxx\n"
                "    Author or Organization. (Year). Title of report. Publisher. URL"
            ),
        )
        methodology["release_history"] = st.text_area("Release history", methodology["release_history"], height=95)
        methodology["citation"] = st.text_area(
            "Citation",
            methodology["citation"],
            height=150,
            help=(
                "Use unindented lines as citation group labels. Put each citation on an indented line "
                "under its group to render a hanging indent on the public methodology page. The examples use APA style."
            ),
            placeholder=(
                "Example dataset:\n"
                "    Author or Organization. (Year). Starter shade review sample (Version number) [Data set]. Publisher. URL\n\n"
                "Transit data:\n"
                "    Author or Organization. (Year). Title of dataset (Version number) [Data set]. Publisher. URL\n\n"
                "Methods and references:\n"
                "    Author, A. A., & Author, B. B. (Year). Title of article. Title of Journal, volume(issue), page range. https://doi.org/xxxxx\n"
                "    Author or Organization. (Year). Title of report. Publisher. URL"
            ),
        )
