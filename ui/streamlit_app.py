"""Streamlit front end for the DemoBank Loan Eligibility Assistant.

Educational demonstration. Synthetic data only.

Run with:  streamlit run ui/streamlit_app.py
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000").rstrip("/")
REQUEST_TIMEOUT = 120

OUTCOME_STYLE = {
    "ELIGIBLE": ("✅", "success"),
    "ELIGIBLE_WITH_CONDITIONS": ("🟡", "warning"),
    "NOT_ELIGIBLE": ("⛔", "error"),
    "INSUFFICIENT_INFORMATION": ("ℹ️", "info"),
}

STATUS_ICON = {"PASS": "✅", "FAIL": "⛔", "UNKNOWN": "❓", "NOT_APPLICABLE": "➖"}

st.set_page_config(
    page_title="DemoBank Loan Eligibility Assistant",
    page_icon="🏦",
    layout="wide",
)


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------
def api_get(path: str, **params: Any) -> Any:
    response = requests.get(f"{API_BASE_URL}{path}", params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def api_post(path: str, payload: Dict[str, Any]) -> Any:
    response = requests.post(f"{API_BASE_URL}{path}", json=payload, timeout=REQUEST_TIMEOUT)
    if response.status_code >= 400:
        raise RuntimeError(f"{response.status_code}: {response.text}")
    return response.json()


def stream_sse(path: str, payload: Dict[str, Any]) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """Yield (event, data) pairs from a server-sent events endpoint."""
    with requests.post(
        f"{API_BASE_URL}{path}",
        json=payload,
        stream=True,
        timeout=REQUEST_TIMEOUT,
        headers={"Accept": "text/event-stream"},
    ) as response:
        response.raise_for_status()
        event = "message"
        for raw in response.iter_lines(decode_unicode=True):
            if raw is None:
                continue
            line = raw.strip()
            if not line:
                event = "message"
                continue
            if line.startswith("event:"):
                event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                body = line[len("data:") :].strip()
                try:
                    yield event, json.loads(body)
                except json.JSONDecodeError:
                    yield event, {"raw": body}


@st.cache_data(ttl=60)
def load_health() -> Dict[str, Any]:
    return api_get("/health")


@st.cache_data(ttl=60)
def load_versions() -> List[Dict[str, Any]]:
    return api_get("/policies/versions")


# ---------------------------------------------------------------------------
# Applicant form
# ---------------------------------------------------------------------------
DEFAULT_APPLICANT: Dict[str, Any] = {
    "applicant_reference": "DEMO-A-1042",
    "age": 34,
    "residency_status": "CITIZEN",
    "employment_status": "FULL_TIME",
    "employment_months": 48,
    "annual_income": 90000.0,
    "other_annual_income": 0.0,
    "existing_monthly_debt": 400.0,
    "credit_score": 720,
    "credit_history_months": 120,
    "active_defaults": 0,
    "has_prior_bankruptcy": False,
    "years_since_bankruptcy": None,
    "liquid_savings": 15000.0,
    "documents_verified": True,
    "is_first_time_buyer": False,
    "requested_amount": 20000.0,
    "loan_term_months": 60,
    "property_value": None,
    "vehicle_value": None,
    "down_payment": None,
}

if "applicant" not in st.session_state:
    st.session_state["applicant"] = dict(DEFAULT_APPLICANT)


def applicant_form(product: str) -> Dict[str, Any]:
    applicant = st.session_state["applicant"]
    with st.sidebar:
        st.subheader("Applicant (synthetic)")
        applicant["applicant_reference"] = st.text_input(
            "Pseudonymous reference", value=applicant.get("applicant_reference") or "DEMO-APPLICANT"
        )
        applicant["age"] = st.number_input(
            "Age", min_value=18, max_value=100, value=int(applicant.get("age") or 34)
        )
        applicant["residency_status"] = st.selectbox(
            "Residency status",
            ["CITIZEN", "PERMANENT_RESIDENT", "LONG_TERM_VISA", "SHORT_TERM_VISA", "NON_RESIDENT"],
            index=["CITIZEN", "PERMANENT_RESIDENT", "LONG_TERM_VISA", "SHORT_TERM_VISA", "NON_RESIDENT"].index(
                applicant.get("residency_status") or "CITIZEN"
            ),
        )
        applicant["employment_status"] = st.selectbox(
            "Employment status",
            ["FULL_TIME", "PART_TIME", "SELF_EMPLOYED", "CONTRACT", "RETIRED", "UNEMPLOYED", "STUDENT"],
            index=["FULL_TIME", "PART_TIME", "SELF_EMPLOYED", "CONTRACT", "RETIRED", "UNEMPLOYED", "STUDENT"].index(
                applicant.get("employment_status") or "FULL_TIME"
            ),
        )
        applicant["employment_months"] = st.number_input(
            "Months in current employment",
            min_value=0,
            max_value=600,
            value=int(applicant.get("employment_months") or 0),
        )
        applicant["annual_income"] = st.number_input(
            "Gross annual income",
            min_value=0.0,
            step=1000.0,
            value=float(applicant.get("annual_income") or 0.0),
        )
        applicant["existing_monthly_debt"] = st.number_input(
            "Existing monthly commitments",
            min_value=0.0,
            step=50.0,
            value=float(applicant.get("existing_monthly_debt") or 0.0),
        )
        use_score = st.checkbox(
            "Internal credit score available", value=applicant.get("credit_score") is not None
        )
        applicant["credit_score"] = (
            int(
                st.number_input(
                    "Internal credit score",
                    min_value=300,
                    max_value=900,
                    value=int(applicant.get("credit_score") or 700),
                )
            )
            if use_score
            else None
        )
        applicant["credit_history_months"] = st.number_input(
            "Months of credit history",
            min_value=0,
            max_value=600,
            value=int(applicant.get("credit_history_months") or 0),
        )
        applicant["active_defaults"] = st.number_input(
            "Active defaults", min_value=0, max_value=20, value=int(applicant.get("active_defaults") or 0)
        )
        applicant["has_prior_bankruptcy"] = st.checkbox(
            "Prior bankruptcy recorded", value=bool(applicant.get("has_prior_bankruptcy"))
        )
        applicant["years_since_bankruptcy"] = (
            float(
                st.number_input(
                    "Years since discharge",
                    min_value=0.0,
                    max_value=40.0,
                    step=0.5,
                    value=float(applicant.get("years_since_bankruptcy") or 0.0),
                )
            )
            if applicant["has_prior_bankruptcy"]
            else None
        )
        applicant["liquid_savings"] = st.number_input(
            "Liquid savings", min_value=0.0, step=500.0, value=float(applicant.get("liquid_savings") or 0.0)
        )
        applicant["documents_verified"] = st.checkbox(
            "Supporting evidence verified", value=bool(applicant.get("documents_verified"))
        )

        st.markdown("**Facility**")
        applicant["requested_amount"] = st.number_input(
            "Requested amount",
            min_value=0.0,
            step=1000.0,
            value=float(applicant.get("requested_amount") or 0.0),
        )
        applicant["loan_term_months"] = st.number_input(
            "Term (months)",
            min_value=6,
            max_value=480,
            value=int(applicant.get("loan_term_months") or 60),
        )
        if product == "MORTGAGE":
            applicant["property_value"] = st.number_input(
                "Property value",
                min_value=0.0,
                step=5000.0,
                value=float(applicant.get("property_value") or 400000.0),
            )
            applicant["down_payment"] = st.number_input(
                "Deposit", min_value=0.0, step=1000.0, value=float(applicant.get("down_payment") or 40000.0)
            )
            applicant["is_first_time_buyer"] = st.checkbox(
                "First-time buyer", value=bool(applicant.get("is_first_time_buyer"))
            )
        elif product == "AUTO_LOAN":
            applicant["vehicle_value"] = st.number_input(
                "Vehicle value",
                min_value=0.0,
                step=1000.0,
                value=float(applicant.get("vehicle_value") or 32000.0),
            )

    st.session_state["applicant"] = applicant
    return {key: value for key, value in applicant.items() if value is not None}


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------
def render_outcome(decision: Dict[str, Any]) -> None:
    outcome = decision.get("outcome", "UNKNOWN")
    icon, level = OUTCOME_STYLE.get(outcome, ("•", "info"))
    getattr(st, level)(f"{icon} **{outcome}** — {decision.get('headline', '')}")

    columns = st.columns(4)
    columns[0].metric("Policy version applied", decision.get("policy_version", "-"))
    payment = decision.get("estimated_monthly_payment")
    columns[1].metric(
        "Indicative repayment", f"{payment:,.2f}" if isinstance(payment, (int, float)) else "-"
    )
    capacity = decision.get("max_eligible_amount")
    columns[2].metric(
        "Indicative capacity", f"{capacity:,.0f}" if isinstance(capacity, (int, float)) else "-"
    )
    columns[3].metric("Decision hash", str(decision.get("decision_hash", ""))[:12] or "-")


def render_rules(rules: List[Dict[str, Any]]) -> None:
    if not rules:
        return
    frame = pd.DataFrame(
        [
            {
                "": STATUS_ICON.get(rule.get("status", ""), ""),
                "Criterion": rule.get("id"),
                "Clause": rule.get("section"),
                "Severity": rule.get("severity"),
                "Status": rule.get("status"),
                "Detail": rule.get("detail"),
            }
            for rule in rules
        ]
    )
    st.dataframe(frame, use_container_width=True, hide_index=True)


def render_citations(citations: List[Dict[str, Any]]) -> None:
    if not citations:
        st.caption("No clauses retrieved.")
        return
    for citation in citations:
        header = (
            f"{citation['marker']} {citation['document_id']} {citation['section']} "
            f"(policy v{citation['policy_version']})"
        )
        with st.expander(header):
            st.write(citation.get("quote", ""))
            meta = []
            if citation.get("rule_ids"):
                meta.append("supports " + ", ".join(citation["rule_ids"]))
            if citation.get("score") is not None:
                meta.append(f"retrieval score {citation['score']:.3f}")
            if citation.get("source_path"):
                meta.append(citation["source_path"])
            if meta:
                st.caption(" · ".join(meta))


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
def tab_prequalification(product: str, policy_choice: Optional[str]) -> None:
    applicant = applicant_form(product)
    question = st.text_input(
        "Applicant question",
        value="Am I eligible for this amount, and what would change the answer?",
    )

    left, right = st.columns([1, 1])
    run_stream = left.button("Run pre-qualification (streaming)", type="primary")
    run_plain = right.button("Deterministic check only (no LLM)")

    payload = {
        "applicant": applicant,
        "product": product,
        "question": question,
    }
    if policy_choice:
        payload["policy_version"] = policy_choice

    if run_plain:
        try:
            result = api_post("/eligibility/evaluate", payload)
        except RuntimeError as exc:
            st.error(str(exc))
            return
        render_outcome(result["decision"])
        st.subheader("Criteria")
        render_rules(
            [
                {
                    "id": rule["rule_id"],
                    "section": rule["policy_reference"]["section"],
                    "severity": rule["severity"],
                    "status": rule["status"],
                    "detail": rule["detail"],
                }
                for rule in result["decision"]["rule_results"]
            ]
        )
        st.subheader("Citations")
        render_citations(result.get("citations", []))
        st.caption(f"Audit record #{result.get('audit_id')} · trace {result.get('trace_id')}")

    if run_stream:
        decision_box = st.container()
        st.subheader("Explanation")
        explanation_box = st.empty()
        text = ""
        decision: Dict[str, Any] = {}
        citations: List[Dict[str, Any]] = []
        audit_info: Dict[str, Any] = {}
        guardrail: Dict[str, Any] = {}

        try:
            with st.spinner("Evaluating against the applicable policy version..."):
                for event, data in stream_sse("/chat/stream", payload):
                    if event == "decision":
                        decision = data
                        with decision_box:
                            render_outcome(decision)
                    elif event == "citations":
                        citations = data.get("citations", [])
                    elif event == "token":
                        text += data.get("text", "")
                        explanation_box.markdown(text)
                    elif event == "guardrail":
                        guardrail = data
                        if not data.get("ok", True):
                            text = data.get("corrected_explanation", text)
                            explanation_box.markdown(text)
                    elif event == "audit":
                        audit_info = data
                    elif event == "error":
                        st.error(data.get("message", "stream failed"))
        except requests.RequestException as exc:
            st.error(f"Streaming failed: {exc}")
            return

        if decision:
            st.subheader("Criteria")
            render_rules(decision.get("rules", []))
            with st.expander("Values relied upon"):
                st.json(decision.get("computed_facts", {}) or decision)
        st.subheader("Citations")
        render_citations(citations)

        if guardrail and not guardrail.get("ok", True):
            st.warning(
                "Guardrails replaced the generated explanation: "
                + "; ".join(guardrail.get("violations", []))
            )
        if audit_info:
            st.caption(
                f"Audit record #{audit_info.get('audit_id')} · explanation mode "
                f"{audit_info.get('explanation_mode')} · model {audit_info.get('llm_model') or 'n/a'} "
                f"· decision hash {str(audit_info.get('decision_hash', ''))[:16]}"
            )


def tab_documents(product: str) -> None:
    st.write(
        "Upload a synthetic applicant pack (PDF, TXT, MD, CSV, JSON). Extracted values "
        "carry provenance and can be pushed into the applicant form."
    )
    try:
        samples = api_get("/documents/samples").get("documents", [])
    except Exception:
        samples = []

    chosen = st.selectbox("Bundled synthetic document", ["(none)"] + samples)
    uploaded = st.file_uploader("...or upload your own", type=["pdf", "txt", "md", "csv", "json"])

    result: Optional[Dict[str, Any]] = None
    if st.button("Extract applicant information"):
        try:
            if uploaded is not None:
                response = requests.post(
                    f"{API_BASE_URL}/documents/extract",
                    files={"file": (uploaded.name, uploaded.getvalue())},
                    timeout=REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                result = response.json()
            elif chosen and chosen != "(none)":
                result = api_post(f"/documents/samples/{chosen}/extract", {})
            else:
                st.info("Choose a bundled document or upload a file first.")
        except Exception as exc:
            st.error(f"Extraction failed: {exc}")

    if result:
        st.session_state["extraction"] = result

    result = st.session_state.get("extraction")
    if not result:
        return

    st.success(
        f"Parsed {result['source_document']} with {result['parser']} "
        f"({result['page_count']} page(s), {result['character_count']} characters)."
    )
    if result.get("fields"):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Field": field["field_name"],
                        "Value": field["value"],
                        "Confidence": field["confidence"],
                        "Page": (field.get("provenance") or {}).get("page"),
                        "Source line": (field.get("provenance") or {}).get("snippet"),
                    }
                    for field in result["fields"]
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
    if result.get("missing_fields"):
        st.warning("Still missing: " + ", ".join(result["missing_fields"]))
    for warning in result.get("warnings", []):
        st.caption(f"⚠️ {warning}")

    if st.button("Use these values in the applicant form"):
        current = dict(st.session_state["applicant"])
        current.update({k: v for k, v in result["applicant"].items() if v is not None})
        st.session_state["applicant"] = current
        st.success("Applicant form updated. Switch to the Pre-qualification tab.")


def tab_policy_explorer() -> None:
    versions = load_versions()
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Version": version["policy_version"],
                    "Status": version["status"],
                    "Effective from": version["effective_from"],
                    "Effective to": version["effective_to"] or "current",
                    "Criteria": version["rule_count"],
                    "Products": ", ".join(version["products"]),
                }
                for version in versions
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

    selected = st.selectbox(
        "Policy version", [version["policy_version"] for version in versions], index=len(versions) - 1
    )
    info = next(v for v in versions if v["policy_version"] == selected)
    if info.get("change_log"):
        with st.expander(f"Change log for v{selected}", expanded=True):
            for entry in info["change_log"]:
                st.markdown(f"- {entry}")

    query = st.text_input("Ask the policy corpus", value="What is the maximum debt to income ratio?")
    if st.button("Search policy"):
        try:
            results = api_get("/policies/search", q=query, policy_version=selected, top_k=5)
        except Exception as exc:
            st.error(f"Search failed: {exc}")
            return
        for chunk in results["chunks"]:
            with st.expander(
                f"{chunk['document_id']} {chunk['section']} · score {chunk['score']:.3f}"
            ):
                st.write(chunk["text"])


def tab_version_comparison(product: str) -> None:
    st.write(
        "The same applicant, evaluated against every registered policy version. This is how "
        "the assistant answers \"why did the answer change?\"."
    )
    applicant = {
        key: value for key, value in st.session_state["applicant"].items() if value is not None
    }
    if st.button("Compare policy versions"):
        try:
            result = api_post(
                "/eligibility/compare-versions", {"applicant": applicant, "product": product}
            )
        except RuntimeError as exc:
            st.error(str(exc))
            return
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Version": entry["policy_version"],
                        "Outcome": entry["outcome"],
                        "Failed mandatory": ", ".join(entry["failed_hard_rules"]) or "-",
                        "Failed supporting": ", ".join(entry["failed_soft_rules"]) or "-",
                        "Unknown": ", ".join(entry["unknown_rules"]) or "-",
                        "Capacity": entry["max_eligible_amount"],
                    }
                    for entry in result["entries"]
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        if result["differences"]:
            st.subheader("What changed")
            for difference in result["differences"]:
                st.markdown(f"- {difference}")
        else:
            st.info("Both policy versions produce the same result for this applicant.")


def tab_audit() -> None:
    try:
        summary = api_get("/audit/summary")
        records = api_get("/audit/decisions", limit=50)
    except Exception as exc:
        st.error(f"Audit log unavailable: {exc}")
        return

    columns = st.columns(len(summary.get("by_outcome", {})) or 1)
    for column, (outcome, count) in zip(columns, summary.get("by_outcome", {}).items()):
        column.metric(outcome, count)
    st.caption(f"{summary.get('total', 0)} audit records in total.")

    if not records:
        st.info("No decisions recorded yet.")
        return

    st.dataframe(
        pd.DataFrame(
            [
                {
                    "#": record["id"],
                    "When": record["created_at"],
                    "Applicant": record["applicant_reference"],
                    "Product": record["product"],
                    "Policy": record["policy_version"],
                    "Outcome": record["outcome"],
                    "Mode": record["explanation_mode"],
                    "Guardrails": len(record.get("guardrail_violations", [])),
                    "Hash": record["decision_hash"][:12],
                }
                for record in records
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

    record_id = st.number_input(
        "Inspect audit record", min_value=1, value=int(records[0]["id"]), step=1
    )
    if st.button("Load record"):
        try:
            record = api_get(f"/audit/decisions/{int(record_id)}")
        except Exception as exc:
            st.error(f"Record unavailable: {exc}")
            return
        st.json(record)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
st.title("🏦 DemoBank Loan Eligibility Assistant")
st.caption(
    "DEMO BANK - SYNTHETIC DOCUMENT - FOR EDUCATIONAL USE ONLY · the deterministic rule "
    "engine decides eligibility; Claude only explains the decision."
)

try:
    health = load_health()
except Exception as exc:  # pragma: no cover - UI guard
    st.error(f"Cannot reach the API at {API_BASE_URL}: {exc}")
    st.stop()

with st.sidebar:
    st.subheader("Enquiry")
    product = st.selectbox("Product", ["PERSONAL_LOAN", "MORTGAGE", "AUTO_LOAN"])
    version_options = ["(default: " + health["default_policy_version"] + ")"] + health[
        "policy_versions"
    ]
    version_choice = st.selectbox("Policy version", version_options)
    policy_choice = None if version_choice.startswith("(default") else version_choice
    st.divider()
    st.caption(
        f"LLM mode: **{health['llm_mode']}**\n\n"
        f"Embeddings: `{health['embedding_backend']}`\n\n"
        f"Vector store: `{health['vector_backend']}`\n\n"
        f"Indexed chunks: {health['indexed_chunks']}\n\n"
        f"Engine: `{health['engine_version']}`"
    )

tabs = st.tabs(
    ["Pre-qualification", "Documents", "Policy explorer", "Version comparison", "Audit log"]
)
with tabs[0]:
    tab_prequalification(product, policy_choice)
with tabs[1]:
    tab_documents(product)
with tabs[2]:
    tab_policy_explorer()
with tabs[3]:
    tab_version_comparison(product)
with tabs[4]:
    tab_audit()
