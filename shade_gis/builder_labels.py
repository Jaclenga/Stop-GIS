import math
import re
from typing import Any

import pandas as pd
import streamlit as st

from shade_gis.shade_dimensions import normalize_shade_coverage, split_shade_sources


REVIEW_STATUS_NAMES = {
    "Unlabeled",
    "Needs Review",
    "Crowd Reviewed",
    "Expert Reviewed",
    "Accepted",
    "Disputed",
    "Archived",
}


def normalize_review_status(value: Any) -> str:
    if value is None or value is pd.NA:
        return "Unlabeled"
    try:
        missing = pd.isna(value)
        if bool(missing):
            return "Unlabeled"
    except (TypeError, ValueError):
        # Nested values are not scalar missing values. They normalize to the
        # same fallback as any other unsupported review status below.
        pass
    try:
        text = str(value).strip()
    except Exception:
        return "Needs Review"
    if not text:
        return "Unlabeled"
    return text if text in REVIEW_STATUS_NAMES else "Needs Review"


def normalize_review_status_series(values: pd.Series) -> pd.Series:
    """Normalize statuses without rebuilding an Arrow-backed string array."""
    object_values = values.astype(object)
    normalized = [normalize_review_status(value) for value in object_values.tolist()]
    return pd.Series(normalized, index=values.index, dtype=object, name=values.name)


def stop_picker_label(row: pd.Series) -> str:
    stop_id = str(row.get("stop_id", "")).strip()
    stop_name = str(row.get("stop_name", "")).strip() or "Unnamed stop"
    routes = str(row.get("routes", "")).strip()
    suffix = f" | routes {routes}" if routes else ""
    return f"{stop_name} ({stop_id}){suffix}"


def taxonomy_names(taxonomy: list[dict[str, Any]]) -> list[str]:
    names = [str(item.get("name", "")).strip() for item in taxonomy if str(item.get("name", "")).strip()]
    return names or ["Needs Review"]


def label_source_code(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "manual").strip().lower()).strip("_") or "manual"


def raw_label_summary(labels: pd.DataFrame, stops: pd.DataFrame) -> pd.DataFrame:
    labeled_stop_count = labels["stop_id"].nunique() if not labels.empty and "stop_id" in labels.columns else 0
    conflicting_stops = 0
    if not labels.empty and {"stop_id", "shade_category"}.issubset(labels.columns):
        category_counts = labels.dropna(subset=["shade_category"]).groupby("stop_id")["shade_category"].nunique()
        conflicting_stops = int((category_counts > 1).sum())
    checks = [
        ("Raw labels stored", len(labels)),
        ("Stops with raw labels", int(labeled_stop_count)),
        ("Stops without raw labels", max(len(stops) - int(labeled_stop_count), 0)),
        ("Stops with conflicting raw labels", conflicting_stops),
    ]
    return pd.DataFrame(checks, columns=["Check", "Value"])


def clean_label_values(labels: pd.DataFrame, label_column: str = "shade_category") -> pd.DataFrame:
    if labels.empty or label_column not in labels.columns or "stop_id" not in labels.columns:
        return pd.DataFrame(columns=list(labels.columns) if not labels.empty else ["stop_id", label_column])
    clean = labels.copy()
    clean["stop_id"] = clean["stop_id"].fillna("").astype(str).str.strip()
    label_values = clean[label_column].fillna("").astype(str)
    if label_column == "shade_category" and "shade_coverage" in clean.columns:
        coverage_values = clean["shade_coverage"].fillna("").astype(str).str.strip()
        label_values = coverage_values.where(coverage_values != "", label_values)
    clean[label_column] = label_values.map(
        lambda value: normalize_shade_coverage(value, "")
    )
    clean = clean[(clean["stop_id"] != "") & (clean[label_column] != "")]
    return clean


def majority_label_table(labels: pd.DataFrame, label_column: str = "shade_category") -> pd.DataFrame:
    clean = clean_label_values(labels, label_column)
    if clean.empty:
        return pd.DataFrame(
            columns=[
                "stop_id",
                "majority_label",
                "label_count",
                "majority_count",
                "agreement_pct",
                "disagreement_flag",
                "tied_majority",
            ]
        )
    rows = []
    for stop_id, group in clean.groupby("stop_id", sort=True):
        counts = group[label_column].value_counts()
        max_count = int(counts.max())
        winners = sorted(counts[counts == max_count].index.astype(str).tolist())
        total = int(counts.sum())
        rows.append(
            {
                "stop_id": stop_id,
                "majority_label": "; ".join(winners),
                "label_count": total,
                "majority_count": max_count,
                "agreement_pct": round(max_count / total * 100, 1) if total else 0.0,
                "disagreement_flag": len(counts) > 1,
                "tied_majority": len(winners) > 1,
            }
        )
    return pd.DataFrame(rows)


def label_rater_key(row: pd.Series) -> str:
    labeler_id = str(row.get("labeler_id", "") or "").strip()
    if labeler_id:
        return labeler_id
    role = str(row.get("labeler_role", "") or "").strip()
    source = str(row.get("source", "") or "").strip()
    return f"{role or 'unknown'}:{source or 'manual'}"


def latest_labels_by_rater(labels: pd.DataFrame, label_column: str = "shade_category") -> pd.DataFrame:
    clean = clean_label_values(labels, label_column)
    if clean.empty:
        return pd.DataFrame(columns=["stop_id", "rater", label_column])
    clean = clean.copy()
    clean["rater"] = clean.apply(label_rater_key, axis=1)
    if "created_at" in clean.columns:
        clean = clean.sort_values("created_at")
    return clean.drop_duplicates(subset=["stop_id", "rater"], keep="last")


def cohen_kappa_for_pair(left: pd.Series, right: pd.Series, categories: list[str]) -> float | None:
    paired = pd.DataFrame({"left": left, "right": right}).dropna()
    if paired.empty:
        return None
    observed = float((paired["left"] == paired["right"]).mean())
    total = len(paired)
    expected = 0.0
    for category in categories:
        expected += (paired["left"].eq(category).sum() / total) * (paired["right"].eq(category).sum() / total)
    if math.isclose(1.0 - expected, 0.0):
        return 1.0 if math.isclose(observed, 1.0) else None
    return (observed - expected) / (1.0 - expected)


def average_pairwise_cohen_kappa(labels: pd.DataFrame, label_column: str = "shade_category") -> tuple[float | None, int]:
    latest = latest_labels_by_rater(labels, label_column)
    if latest.empty or latest["rater"].nunique() < 2:
        return None, 0
    matrix = latest.pivot(index="stop_id", columns="rater", values=label_column)
    categories = sorted(latest[label_column].dropna().astype(str).unique().tolist())
    kappas = []
    raters = list(matrix.columns)
    for left_index, left_rater in enumerate(raters):
        for right_rater in raters[left_index + 1 :]:
            paired = matrix[[left_rater, right_rater]].dropna()
            if len(paired) < 2:
                continue
            kappa = cohen_kappa_for_pair(paired[left_rater], paired[right_rater], categories)
            if kappa is not None:
                kappas.append(kappa)
    if not kappas:
        return None, 0
    return float(sum(kappas) / len(kappas)), len(kappas)


def category_count_matrix(labels: pd.DataFrame, label_column: str = "shade_category") -> pd.DataFrame:
    clean = clean_label_values(labels, label_column)
    if clean.empty:
        return pd.DataFrame()
    return pd.crosstab(clean["stop_id"], clean[label_column])


def fleiss_kappa(labels: pd.DataFrame, label_column: str = "shade_category") -> float | None:
    counts = category_count_matrix(labels, label_column)
    if counts.empty:
        return None
    counts = counts[counts.sum(axis=1) >= 2]
    if counts.empty:
        return None
    item_totals = counts.sum(axis=1)
    total_assignments = float(item_totals.sum())
    p_i = ((counts.pow(2).sum(axis=1) - item_totals) / (item_totals * (item_totals - 1))).fillna(0)
    p_bar = float((p_i * item_totals / total_assignments).sum())
    category_props = counts.sum(axis=0) / total_assignments
    p_e = float(category_props.pow(2).sum())
    if math.isclose(1.0 - p_e, 0.0):
        return 1.0 if math.isclose(p_bar, 1.0) else None
    return (p_bar - p_e) / (1.0 - p_e)


def krippendorff_alpha_nominal(labels: pd.DataFrame, label_column: str = "shade_category") -> float | None:
    counts = category_count_matrix(labels, label_column)
    if counts.empty:
        return None
    counts = counts[counts.sum(axis=1) >= 2]
    if counts.empty:
        return None
    item_totals = counts.sum(axis=1)
    observed_terms = []
    for stop_id, row in counts.iterrows():
        n_i = float(item_totals.loc[stop_id])
        observed_terms.append(float((row * (n_i - row)).sum() / (n_i - 1)))
    observed = sum(observed_terms) / float(item_totals.sum())
    category_totals = counts.sum(axis=0)
    total = float(category_totals.sum())
    if total <= 1:
        return None
    expected = float((category_totals * (total - category_totals)).sum() / (total - 1) / total)
    if math.isclose(expected, 0.0):
        return 1.0 if math.isclose(observed, 0.0) else None
    return 1.0 - (observed / expected)


def format_metric_value(value: float | None, digits: int = 3) -> str:
    if value is None or pd.isna(value):
        return "Not enough data"
    return f"{float(value):.{digits}f}"


def agreement_metric_summary(labels: pd.DataFrame, stops: pd.DataFrame) -> pd.DataFrame:
    majority = majority_label_table(labels)
    labeled_stops = int(majority["stop_id"].nunique()) if not majority.empty else 0
    multi_label_stops = int((majority["label_count"] >= 2).sum()) if not majority.empty else 0
    disagreement_stops = int(majority["disagreement_flag"].sum()) if not majority.empty else 0
    average_agreement = (
        float(majority["agreement_pct"].mean()) if not majority.empty and "agreement_pct" in majority else None
    )
    cohen, cohen_pairs = average_pairwise_cohen_kappa(labels)
    summary = pd.DataFrame(
        [
            ("Stops with labels", labeled_stops),
            ("Stops with 2+ labels", multi_label_stops),
            ("Stops with disagreement", disagreement_stops),
            ("Mean majority agreement", f"{average_agreement:.1f}%" if average_agreement is not None else "Not enough data"),
            ("Average pairwise Cohen kappa", format_metric_value(cohen)),
            ("Cohen rater pairs compared", cohen_pairs),
            ("Fleiss kappa", format_metric_value(fleiss_kappa(labels))),
            ("Krippendorff alpha", format_metric_value(krippendorff_alpha_nominal(labels))),
        ],
        columns=["Metric", "Value"],
    )
    summary["Value"] = summary["Value"].astype(str)
    return summary


RESOLVED_REVIEW_STATUSES = {"Accepted", "Expert Reviewed", "Archived"}


def disagreement_queue_table(
    stops: pd.DataFrame,
    labels: pd.DataFrame,
    review_history: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Build the unresolved disagreement queue, reopening stops after newer labels."""
    majority = majority_label_table(labels)
    if majority.empty:
        return pd.DataFrame()
    queue = majority[majority["disagreement_flag"].astype(bool)].copy()
    if queue.empty:
        return queue

    stop_details = stops.copy() if stops is not None else pd.DataFrame()
    if not stop_details.empty and "stop_id" in stop_details.columns:
        stop_details["stop_id"] = stop_details["stop_id"].astype(str)
        queue = queue.merge(stop_details, on="stop_id", how="left")
    for column, fallback in [
        ("stop_name", ""),
        ("routes", ""),
        ("review_status", "Unlabeled"),
    ]:
        if column not in queue.columns:
            queue[column] = fallback
        queue[column] = queue[column].fillna(fallback)

    clean_labels = clean_label_values(labels)
    if "created_at" in clean_labels.columns:
        clean_labels = clean_labels.copy()
        clean_labels["latest_label_at"] = pd.to_datetime(clean_labels["created_at"], errors="coerce", utc=True)
        latest_labels = clean_labels.groupby("stop_id", as_index=False)["latest_label_at"].max()
        queue = queue.merge(latest_labels, on="stop_id", how="left")
    else:
        queue["latest_label_at"] = pd.Series(pd.NaT, index=queue.index, dtype="datetime64[ns, UTC]")

    history = review_history.copy() if review_history is not None else pd.DataFrame()
    if not history.empty and {"stop_id", "to_status"}.issubset(history.columns):
        resolved = history[history["to_status"].isin(RESOLVED_REVIEW_STATUSES)].copy()
        if not resolved.empty:
            resolved["stop_id"] = resolved["stop_id"].astype(str)
            resolved["resolved_at"] = pd.to_datetime(resolved.get("created_at"), errors="coerce", utc=True)
            latest_resolutions = resolved.groupby("stop_id", as_index=False)["resolved_at"].max()
            queue = queue.merge(latest_resolutions, on="stop_id", how="left")
        else:
            queue["resolved_at"] = pd.Series(pd.NaT, index=queue.index, dtype="datetime64[ns, UTC]")
    else:
        queue["resolved_at"] = pd.Series(pd.NaT, index=queue.index, dtype="datetime64[ns, UTC]")

    terminal_without_history = queue["review_status"].isin(RESOLVED_REVIEW_STATUSES) & queue["resolved_at"].isna()
    resolution_is_current = queue["resolved_at"].notna() & (
        queue["latest_label_at"].isna() | (queue["resolved_at"] >= queue["latest_label_at"])
    )
    queue = queue[~(terminal_without_history | resolution_is_current)].copy()
    return queue.sort_values(["agreement_pct", "label_count", "stop_id"], ascending=[True, False, True])


def agreement_overview_metrics(
    stops: pd.DataFrame,
    labels: pd.DataFrame,
    review_history: pd.DataFrame | None = None,
) -> dict[str, int | float | None]:
    majority = majority_label_table(labels)
    queue = disagreement_queue_table(stops, labels, review_history)
    return {
        "stops_labeled": int(majority["stop_id"].nunique()) if not majority.empty else 0,
        "stops_needing_review": len(queue),
        "raw_disagreements": int(majority["disagreement_flag"].sum()) if not majority.empty else 0,
        "mean_agreement": float(majority["agreement_pct"].mean()) if not majority.empty else None,
        "krippendorff_alpha": krippendorff_alpha_nominal(labels),
        "fleiss_kappa": fleiss_kappa(labels),
    }


def render_agreement_metrics(labels: pd.DataFrame, stops: pd.DataFrame) -> None:
    st.markdown("#### Agreement Metrics")
    if labels.empty:
        st.info("Submit labels from at least two assessments to compute agreement metrics.")
        return
    st.dataframe(agreement_metric_summary(labels, stops), width="stretch", hide_index=True)
    majority = majority_label_table(labels)
    if not majority.empty:
        display = majority.sort_values(["disagreement_flag", "agreement_pct", "stop_id"], ascending=[False, True, True])
        st.dataframe(display, width="stretch", hide_index=True)


def split_list_field(value: Any) -> list[str]:
    if value is None:
        return []
    try:
        if pd.isna(value):
            return []
    except (TypeError, ValueError):
        pass
    pieces = re.split(r"[;,]", str(value))
    return [piece.strip() for piece in pieces if piece.strip()]


def stop_review_snapshot(stop: pd.Series | dict[str, Any]) -> dict[str, Any]:
    raw_coverage = stop.get("shade_coverage", "")
    if pd.isna(raw_coverage) or not str(raw_coverage).strip():
        raw_coverage = stop.get("shading", "")
    coverage = normalize_shade_coverage(
        raw_coverage,
        "Needs Review",
    )
    return {
        "shade_category": coverage,
        "shade_coverage": coverage,
        "shade_sources": "; ".join(split_shade_sources(stop.get("shade_sources", ""))),
        "confidence": stop.get("confidence", None),
        "review_status": str(stop.get("review_status", "Unlabeled") or "Unlabeled"),
    }


def review_queue_table(stops: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    if stops.empty or "stop_id" not in stops.columns:
        return pd.DataFrame()
    queue = stops.copy()
    queue["stop_id"] = queue["stop_id"].astype(str)
    if "review_status" not in queue.columns:
        queue["review_status"] = "Unlabeled"
    queue["review_status"] = normalize_review_status_series(queue["review_status"])
    for column in ["shading", "stop_name", "routes", "municipality"]:
        if column not in queue.columns:
            queue[column] = ""

    majority = majority_label_table(labels)
    if not majority.empty:
        majority["stop_id"] = majority["stop_id"].astype(str)
        queue = queue.merge(majority, on="stop_id", how="left")
    for column, fallback in [
        ("majority_label", ""),
        ("label_count", 0),
        ("majority_count", 0),
        ("agreement_pct", 0.0),
        ("disagreement_flag", False),
        ("tied_majority", False),
    ]:
        if column not in queue.columns:
            queue[column] = fallback
        queue[column] = queue[column].fillna(fallback)
    queue["label_count"] = pd.to_numeric(queue["label_count"], errors="coerce").fillna(0).astype(int)
    queue = queue[queue["label_count"] > 0].copy()
    if queue.empty:
        return queue

    priority_rank = {status: index for index, status in enumerate(["Disputed", "Needs Review", "Unlabeled"])}
    queue["queue_rank"] = queue["review_status"].map(priority_rank).fillna(10)
    if "priority_score" in queue.columns:
        priority_score = pd.to_numeric(queue["priority_score"], errors="coerce").fillna(0)
    else:
        priority_score = pd.Series(0.0, index=queue.index)
        queue["priority_score"] = priority_score
    agreement_gap = 100 - pd.to_numeric(queue["agreement_pct"], errors="coerce").fillna(100)
    queue["queue_score"] = (
        (10 - queue["queue_rank"]).clip(lower=0) * 100
        + queue["disagreement_flag"].astype(bool).astype(int) * 60
        + agreement_gap
        + priority_score.clip(lower=0)
    )
    return queue.sort_values(["queue_score", "priority_score", "stop_name"], ascending=[False, False, True])


def review_queue_label(row: pd.Series) -> str:
    status = str(row.get("review_status", "Unlabeled") or "Unlabeled")
    label_count = int(float(row.get("label_count", 0) or 0))
    agreement = float(row.get("agreement_pct", 0) or 0)
    if bool(row.get("disagreement_flag", False)):
        review_state = f"{label_count} labels, disagreement"
    elif label_count:
        review_state = f"{label_count} labels, {agreement:.1f}% agreement"
    else:
        review_state = "no labels yet"
    return f"{stop_picker_label(row)} | {status} | {review_state}"
