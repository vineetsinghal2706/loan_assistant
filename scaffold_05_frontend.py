#!/usr/bin/env python3
"""Scaffolds frontend/: the Streamlit chat UI."""
import os


def write_file(relpath, content):
    root = os.path.dirname(os.path.abspath(__file__))
    full_path = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    print("wrote", relpath)


FILES = {}

FILES["frontend/requirements.txt"] = r'''streamlit==1.38.0
requests==2.32.3
'''

FILES["frontend/Dockerfile"] = r'''FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 8501
CMD ["streamlit", "run", "streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501"]
'''

FILES["frontend/streamlit_app.py"] = r'''import json
import os
import uuid

import requests
import streamlit as st

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")

st.set_page_config(page_title="Loan Eligibility Assistant", page_icon="\U0001F3E6", layout="wide")

if "history" not in st.session_state:
    st.session_state.history = []
if "session_id" not in st.session_state:
    st.session_state.session_id = uuid.uuid4().hex


def get_policy_version():
    try:
        resp = requests.get(f"{BACKEND_URL}/v1/policy/version", timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


with st.sidebar:
    st.header("Policy status")
    info = get_policy_version()
    if "error" in info:
        st.error(f"Backend unreachable: {info['error']}")
    else:
        st.metric("Current rule version", info["current_rule_version"])
        st.caption("Available versions: " + ", ".join(info["available_rule_versions"]))

    st.divider()
    st.header("Applicant profile (optional)")
    st.caption("Fill this in to get a grounded eligibility decision alongside the chat answer.")
    use_profile = st.checkbox("Include applicant profile", value=True)
    applicant = None
    if use_profile:
        monthly_income = st.number_input("Monthly income (Rs)", min_value=0, value=35000, step=1000)
        monthly_debt = st.number_input("Existing monthly debt (Rs)", min_value=0, value=5000, step=500)
        credit_score = st.slider("Credit score", 300, 900, 680)
        age = st.number_input("Age", min_value=18, max_value=100, value=30)
        employment_type = st.selectbox("Employment type", ["salaried", "self_employed", "unemployed"])
        requested_amount = st.number_input("Requested loan amount (Rs)", min_value=0, value=200000, step=10000)
        loan_tenure_months = st.number_input("Loan tenure (months)", min_value=1, value=36)
        applicant = {
            "monthly_income": monthly_income,
            "monthly_debt": monthly_debt,
            "credit_score": credit_score,
            "age": age,
            "employment_type": employment_type,
            "requested_amount": requested_amount,
            "loan_tenure_months": loan_tenure_months,
        }

tab_chat, tab_audit = st.tabs(["Chat", "Audit trail"])

with tab_chat:
    st.title("Loan Eligibility Assistant")
    st.caption(
        "Ask about personal loan eligibility. Answers are grounded in the current policy "
        "documents and cite the rule/section applied."
    )

    for turn in st.session_state.history:
        with st.chat_message(turn["role"]):
            st.markdown(turn["content"])

    question = st.chat_input("e.g. Am I eligible for a personal loan given my profile?")
    if question:
        st.session_state.history.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)

        with st.chat_message("assistant"):
            placeholder = st.empty()
            streamed_text = ""
            citations = []
            decision = None
            error_message = None
            try:
                payload = {
                    "session_id": st.session_state.session_id,
                    "message": question,
                    "applicant": applicant,
                }
                with requests.post(
                    f"{BACKEND_URL}/v1/chat/stream", json=payload, stream=True, timeout=120
                ) as resp:
                    resp.raise_for_status()
                    event_type = None
                    for line in resp.iter_lines(decode_unicode=True):
                        if not line:
                            continue
                        if line.startswith("event:"):
                            event_type = line.split(":", 1)[1].strip()
                        elif line.startswith("data:"):
                            data = json.loads(line.split(":", 1)[1].strip())
                            if event_type == "token":
                                streamed_text += data["text"]
                                placeholder.markdown(streamed_text + "▌")
                            elif event_type == "done":
                                citations = data.get("citations", [])
                                decision = data.get("decision")
                            elif event_type == "error":
                                error_message = data.get("message")
                placeholder.markdown(streamed_text or "_(no content streamed)_")
            except Exception as exc:  # noqa: BLE001
                error_message = str(exc)
                placeholder.markdown(f"Sorry, something went wrong: {error_message}")

            if error_message and streamed_text:
                st.warning(f"The response was interrupted mid-stream: {error_message}")

            if decision:
                outcome_color = {
                    "eligible": "green",
                    "needs_review": "orange",
                    "not_eligible": "red",
                }.get(decision["outcome"], "gray")
                st.markdown(
                    f"**Decision:** :{outcome_color}[{decision['outcome'].upper()}] "
                    f"- rule version `{decision['rule_version']}`"
                )

            if citations:
                with st.expander("Policy citations used to ground this answer"):
                    for c in citations:
                        st.markdown(
                            f"- **[{c['section']}]** - {c['doc_id']} `{c['version']}` "
                            f"(_{c['source_type']} match_)\n\n  {c['excerpt']}"
                        )

        st.session_state.history.append({"role": "assistant", "content": streamed_text})

with tab_audit:
    st.title("Audit trail")
    st.caption("Every decision is logged with the rule version applied, for audit.")
    n = st.slider("Number of recent records", 5, 100, 20)
    try:
        resp = requests.get(f"{BACKEND_URL}/v1/audit/recent", params={"n": n}, timeout=10)
        resp.raise_for_status()
        records = resp.json()
        if records:
            st.dataframe(records, use_container_width=True)
        else:
            st.info("No audit records yet - ask a question in the Chat tab first.")
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not load audit trail: {exc}")
'''

if __name__ == "__main__":
    for path, content in FILES.items():
        write_file(path, content)
    print(f"\n{len(FILES)} files written.")
