import streamlit as st


def render_about_page(
    study_summary: str,
    data_citation: str,
    context_citation: str = "",
    context_metadata_citation: str = "",
) -> None:
    st.title("Stop Audit")
    st.markdown(f"### {study_summary}")
    st.markdown(
        """
        ## About the Study

        This project documents infrastructure, amenities, accessibility, and passenger-comfort conditions at
        transit stops through reproducible data collection, review, visualization, and publication.

        The study combines official stop locations with configured assessment modes. Each project exposes only
        the modes relevant to its protocol while preserving raw independent observations and reviewed values.

        Classifications were based on visible shade coverage of the waiting area in available imagery rather than
        the mere presence of nearby vegetation or structures. Code what visibly shades the waiting area, not what
        might shade it at another time.

        ## Terminology

        | Term | Operational Definition |
        | --- | --- |
        | Waiting Area | The designated location where passengers would reasonably stand or sit while waiting to board the bus, including any bus stop pad, sidewalk immediately adjacent to the bus stop sign, or seating within a bus shelter. Grass, landscaping, roadway, bicycle lanes, and areas not reasonably intended for waiting are excluded. |

        ## Data Sources

        - Transit stop locations, such as GTFS `stops.txt`
        - Expert, field-audit, imported, or community-submitted stop assessments
        - Optional project-specific attributes or GIS overlays supplied by the study team

        ## Assessment values

        Enabled mode values are stored together in `assessment_values` and exposed as individual columns for
        configured maps, filters, summaries, and exports. Each mode may collect its own comment and confidence.

        ## Legacy shade projections

        - `shade_coverage`: the observed or voted amount of shade reaching the waiting area.
        - `shade_sources`: the observed or voted source labels for shade reaching the waiting area.
        - `shading`: the derived coverage label used for legends, summaries, filters, and exports.
        - `review_status`: the current labeling or review workflow state.
        - `confidence`: reviewer, model, or workflow confidence when collected.
        - `ridership`: optional ridership measure when available.

        Additional uploaded columns are preserved as dataset attributes and can be exposed in tables, map hovers,
        public filters, custom charts, colors, and downloads when they contain usable values.

        ## Shade Voting Guide

        ### Shade Source

        | Shade Source | Operational Definition |
        | --- | --- |
        | Natural | Trees, palms, hedges, or other vegetation visibly shade the waiting area |
        | Purpose-built | A designated bus shelter, awning, canopy, overhang, or similar passenger shelter visibly shades the waiting area |
        | Incidental | A nearby building, wall, or other non-shelter built feature visibly shades the waiting area |
        | Natural; Purpose-built; Incidental | More than one source type visibly shades the waiting area |

        ### Shade Coverage

        | Shade Coverage | Operational Definition |
        | --- | --- |
        | No Shade | No shade visibly reaches the waiting area |
        | Limited | Shade visibly reaches part of the waiting area, but does not cover most of it |
        | Significant | Shade visibly covers most of the waiting area or seating area |

        Trees, utility poles, signs, and nearby buildings are not classified as Purpose-built unless they are clearly
        intended to provide passenger shade or weather protection. Nearby buildings that visibly shade the waiting
        area should be coded as Incidental.

        `shade_sources` and `shade_coverage` are stored separately. The map-facing `shading` value mirrors
        coverage, while source labels remain available in their own field for filters, charts, exports, and review.

        ## Classification Examples

        | Visible condition | Shade Source | Shade Coverage |
        | --- | --- | --- |
        | Bus shelter and trees both visibly shade the waiting area | Natural; Purpose-built | Limited or Significant, depending on coverage |
        | Purpose-built bus shelter visibly shades where riders would wait | Purpose-built | Limited or Significant, depending on coverage |
        | Large building casts shade onto the stop but is not intended as passenger shelter | Incidental | Limited or Significant, depending on coverage |
        | Only a small sign or pole shadow reaches the stop | None | None unless it visibly shades the waiting area |
        | Trees are nearby but do not visibly shade the waiting area | None | None |
        | Hedges or shrubs visibly shade the bench or waiting area | Natural | Limited or Significant, depending on coverage |
        | Palms provide partial coverage | Natural | Limited |
        | Large tree crown shades the stop | Natural | Significant |
        """
    )
    if data_citation:
        st.caption(data_citation)
    if context_citation:
        st.caption(context_citation)
    if context_metadata_citation:
        st.caption(context_metadata_citation)
