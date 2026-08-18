import streamlit as st
import pandas as pd
from snowflake.snowpark.context import get_active_session
import sys
sys.path.insert(0, "/")
from helpers.dropdowns import editable_dropdown, get_field_options

st.set_page_config(page_title="Study Planner", layout="wide")
st.title("Study Planner")

session = get_active_session()

st.markdown("Plan future studies by specifying device requirements. The system checks current inventory and reservations to identify availability and conflicts.")

UTILIZATION_CAP = 0.90

# --- Current Inventory Summary ---
st.header("Current Inventory Status")
inventory_summary = session.sql("""
    SELECT 
        d.DEVICE_TYPE,
        d.VARIANT,
        COUNT(*) AS TOTAL,
        FLOOR(COUNT(*) * 0.9) AS MAX_PLANNABLE,
        SUM(CASE WHEN d.DEVICE_ID IN (
            SELECT DEVICE_ID FROM SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS 
            WHERE STATUS = 'ACTIVE'
        ) THEN 1 ELSE 0 END) AS IN_ACTIVE_STUDY,
        SUM(CASE WHEN d.DEVICE_ID IN (
            SELECT DEVICE_ID FROM SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS 
            WHERE STATUS = 'RESERVED'
        ) THEN 1 ELSE 0 END) AS RESERVED_ASSIGNED,
        COALESCE((
            SELECT SUM(r.RESERVED_COUNT) 
            FROM SCRATCH.HARDWARE_TRACKER.STUDY_RESERVATIONS r
            WHERE r.DEVICE_TYPE = d.DEVICE_TYPE
            AND (r.VARIANT = d.VARIANT OR r.VARIANT IS NULL OR r.VARIANT = 'Any')
        ), 0) AS PLANNED_RESERVED
    FROM SCRATCH.HARDWARE_TRACKER.DEVICES d
    WHERE d.CONDITION_STATUS != 'Retired'
    AND COALESCE(d.AVAILABILITY_STATUS, 'Available') = 'Available'
    GROUP BY d.DEVICE_TYPE, d.VARIANT
    ORDER BY d.DEVICE_TYPE, d.VARIANT
""").to_pandas()

if len(inventory_summary) > 0:
    st.dataframe(inventory_summary, use_container_width=True, hide_index=True)
else:
    st.info("No devices in inventory.")

# --- Current Reservations ---
st.header("Current Reservations")
reservations_df = session.sql("""
    SELECT 
        r.RESERVATION_ID,
        s.STUDY_NAME,
        r.DEVICE_TYPE,
        r.VARIANT,
        r.RESERVED_COUNT,
        r.CREATED_AT
    FROM SCRATCH.HARDWARE_TRACKER.STUDY_RESERVATIONS r
    JOIN SCRATCH.HARDWARE_TRACKER.STUDIES s ON r.STUDY_ID = s.STUDY_ID
    ORDER BY s.STUDY_NAME, r.DEVICE_TYPE
""").to_pandas()

if len(reservations_df) > 0:
    st.dataframe(reservations_df[["STUDY_NAME", "DEVICE_TYPE", "VARIANT", "RESERVED_COUNT", "CREATED_AT"]], 
                 use_container_width=True, hide_index=True)
    cancel_options = [
        f"{row['STUDY_NAME']} - {row['DEVICE_TYPE']} ({row['VARIANT'] or 'Any'}) x{row['RESERVED_COUNT']}"
        for _, row in reservations_df.iterrows()
    ]
    cancel_selection = st.multiselect("Select reservations to cancel", cancel_options)
    if st.button("Cancel Selected Reservations") and cancel_selection:
        for sel in cancel_selection:
            idx = cancel_options.index(sel)
            res_id = reservations_df.iloc[idx]["RESERVATION_ID"]
            session.sql(f"DELETE FROM SCRATCH.HARDWARE_TRACKER.STUDY_RESERVATIONS WHERE RESERVATION_ID = '{res_id}'").collect()
        st.success(f"Cancelled {len(cancel_selection)} reservation(s).")
        st.rerun()
else:
    st.info("No device reservations yet.")

# --- Planned Studies Timeline ---
st.header("Planned Studies")
planned = session.sql("""
    SELECT s.STUDY_NAME, s.PRINCIPAL_INVESTIGATOR, s.START_DATE, s.END_DATE, s.STATUS,
        (SELECT COUNT(*) FROM SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS a 
         WHERE a.STUDY_ID = s.STUDY_ID AND a.STATUS = 'RESERVED') AS ASSIGNED_RESERVED,
        (SELECT COALESCE(SUM(r.RESERVED_COUNT), 0) FROM SCRATCH.HARDWARE_TRACKER.STUDY_RESERVATIONS r
         WHERE r.STUDY_ID = s.STUDY_ID) AS PLANNED_RESERVED
    FROM SCRATCH.HARDWARE_TRACKER.STUDIES s
    WHERE s.STATUS IN ('PLANNED', 'ACTIVE')
    ORDER BY s.START_DATE ASC NULLS LAST
""").to_pandas()

if len(planned) > 0:
    st.dataframe(planned, use_container_width=True, hide_index=True)
else:
    st.info("No planned or active studies.")

# --- Capacity Planning ---
st.header("Capacity Planning")
st.markdown("Check if you have enough devices for a planned study and reserve them. A 90% utilization cap is enforced per device type.")

# Study selection
existing_studies = session.sql("""
    SELECT STUDY_ID, STUDY_NAME FROM SCRATCH.HARDWARE_TRACKER.STUDIES
    WHERE STATUS IN ('PLANNED', 'ACTIVE')
    ORDER BY STUDY_NAME
""").to_pandas()

study_options = ["-- Create new study --"] + existing_studies["STUDY_NAME"].tolist() if len(existing_studies) > 0 else ["-- Create new study --"]
selected_study = st.selectbox("Select study", study_options, key="plan_study_select")

new_study_name = ""
if selected_study == "-- Create new study --":
    new_study_name = st.text_input("New study name", key="new_study_name")

with st.form("capacity_check"):
    st.subheader("Device Requirements")
    requirements = []
    for i in range(5):
        col1, col2, col3 = st.columns([2, 2, 1])
        with col1:
            req_type = st.selectbox(f"Type #{i+1}", [""] + get_field_options("device_type"), key=f"req_type_{i}")
        with col2:
            req_variant = st.selectbox(f"Variant #{i+1}", ["", "Any"] + get_field_options("variant"), key=f"req_variant_{i}")
        with col3:
            req_count = st.number_input(f"Count #{i+1}", min_value=0, value=0, key=f"req_count_{i}")
        if req_type and req_count > 0:
            requirements.append({"type": req_type, "variant": req_variant, "count": req_count})

    check_date_start = st.date_input("Study start date", value=None, key="plan_start")
    check_date_end = st.date_input("Study end date", value=None, key="plan_end")

    submitted = st.form_submit_button("Check Availability & Reserve")

if submitted:
    # Validate study selection
    study_name = new_study_name.strip() if selected_study == "-- Create new study --" else selected_study
    if not study_name:
        st.warning("Please select or enter a study name.")
    elif not requirements:
        st.warning("Add at least one device requirement.")
    else:
        st.subheader("Availability Results (90% cap enforced)")
        all_met = True
        results = []

        for req in requirements:
            variant_filter_sql = f"AND d.VARIANT = '{req['variant']}'" if req["variant"] and req["variant"] != "Any" else ""
            variant_filter_res = f"AND r.VARIANT = '{req['variant']}'" if req["variant"] and req["variant"] != "Any" else "AND (r.VARIANT IS NULL OR r.VARIANT = 'Any')" if not req["variant"] or req["variant"] == "Any" else ""

            # Total non-retired, available inventory for this type/variant
            total_q = f"""
                SELECT COUNT(*) AS CNT FROM SCRATCH.HARDWARE_TRACKER.DEVICES d
                WHERE d.DEVICE_TYPE = '{req['type']}' {variant_filter_sql}
                AND d.CONDITION_STATUS != 'Retired'
                AND COALESCE(d.AVAILABILITY_STATUS, 'Available') = 'Available'
            """
            total_count = session.sql(total_q).collect()[0]["CNT"]
            max_plannable = int(total_count * UTILIZATION_CAP)

            # Already committed: active/reserved device-level assignments
            committed_q = f"""
                SELECT COUNT(*) AS CNT FROM SCRATCH.HARDWARE_TRACKER.DEVICES d
                WHERE d.DEVICE_TYPE = '{req['type']}' {variant_filter_sql}
                AND d.CONDITION_STATUS != 'Retired'
                AND COALESCE(d.AVAILABILITY_STATUS, 'Available') = 'Available'
                AND d.DEVICE_ID IN (
                    SELECT DEVICE_ID FROM SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS
                    WHERE STATUS IN ('ACTIVE', 'RESERVED')
                )
            """
            committed_count = session.sql(committed_q).collect()[0]["CNT"]

            # Already reserved via planning (from STUDY_RESERVATIONS, exclude current study if re-planning)
            if selected_study != "-- Create new study --" and len(existing_studies) > 0:
                current_study_id = existing_studies[existing_studies["STUDY_NAME"] == selected_study]["STUDY_ID"].values[0]
                exclude_clause = f"AND r.STUDY_ID != '{current_study_id}'"
            else:
                exclude_clause = ""

            variant_res_filter = f"AND r.VARIANT = '{req['variant']}'" if req["variant"] and req["variant"] != "Any" else ""
            reserved_plan_q = f"""
                SELECT COALESCE(SUM(r.RESERVED_COUNT), 0) AS CNT 
                FROM SCRATCH.HARDWARE_TRACKER.STUDY_RESERVATIONS r
                WHERE r.DEVICE_TYPE = '{req['type']}' {variant_res_filter} {exclude_clause}
            """
            reserved_plan_count = session.sql(reserved_plan_q).collect()[0]["CNT"]

            already_used = committed_count + reserved_plan_count
            available_for_planning = max(0, max_plannable - already_used)

            variant_label = f" ({req['variant']})" if req["variant"] and req["variant"] != "Any" else ""
            needed = req["count"]

            if available_for_planning >= needed:
                st.success(f"{req['type']}{variant_label}: {available_for_planning} plannable (of {total_count} total, 90% cap = {max_plannable}), {needed} needed")
                results.append(req)
            else:
                st.error(f"{req['type']}{variant_label}: {available_for_planning} plannable (of {total_count} total, 90% cap = {max_plannable}), {needed} needed - SHORTFALL of {needed - available_for_planning}")
                all_met = False

        if all_met:
            st.session_state["reservation_ready"] = True
            st.session_state["reservation_requirements"] = results
            st.session_state["reservation_study_name"] = study_name
            st.session_state["reservation_study_existing"] = selected_study != "-- Create new study --"
            if selected_study != "-- Create new study --" and len(existing_studies) > 0:
                st.session_state["reservation_study_id"] = existing_studies[existing_studies["STUDY_NAME"] == selected_study]["STUDY_ID"].values[0]
            st.session_state["reservation_dates"] = (check_date_start, check_date_end)
            st.success("All requirements can be met! Click 'Confirm Reservation' below to reserve.")
        else:
            st.session_state["reservation_ready"] = False
            st.warning("Some requirements cannot be met within the 90% cap. Consider adjusting requirements or freeing up devices.")

# Reserve button (outside the form)
if st.session_state.get("reservation_ready"):
    if st.button("Confirm Reservation", type="primary"):
        study_name = st.session_state["reservation_study_name"]
        reqs = st.session_state["reservation_requirements"]
        is_existing = st.session_state["reservation_study_existing"]
        dates = st.session_state["reservation_dates"]

        # Get or create study
        if is_existing:
            study_id = st.session_state["reservation_study_id"]
        else:
            session.sql(f"""
                INSERT INTO SCRATCH.HARDWARE_TRACKER.STUDIES (STUDY_NAME, STATUS, START_DATE, END_DATE)
                SELECT '{study_name}', 'PLANNED', 
                    {f"'{dates[0]}'" if dates[0] else "NULL"},
                    {f"'{dates[1]}'" if dates[1] else "NULL"}
            """).collect()
            study_id = session.sql(f"""
                SELECT STUDY_ID FROM SCRATCH.HARDWARE_TRACKER.STUDIES 
                WHERE STUDY_NAME = '{study_name}' ORDER BY CREATED_AT DESC LIMIT 1
            """).collect()[0]["STUDY_ID"]

        # Insert reservations
        for req in reqs:
            variant_val = f"'{req['variant']}'" if req["variant"] and req["variant"] != "Any" else "NULL"
            session.sql(f"""
                INSERT INTO SCRATCH.HARDWARE_TRACKER.STUDY_RESERVATIONS (STUDY_ID, DEVICE_TYPE, VARIANT, RESERVED_COUNT)
                SELECT '{study_id}', '{req['type']}', {variant_val}, {req['count']}
            """).collect()

        st.success(f"Reserved devices for study '{study_name}'!")
        st.session_state["reservation_ready"] = False
        st.rerun()

# --- Conflict Detection ---
st.header("Conflict Detection")
st.markdown("Check for devices double-booked across overlapping studies.")

conflicts = session.sql("""
    SELECT 
        d.SERIAL_NUMBER, d.DEVICE_TYPE, d.VARIANT,
        s1.STUDY_NAME AS STUDY_1, s1.START_DATE AS S1_START, s1.END_DATE AS S1_END,
        s2.STUDY_NAME AS STUDY_2, s2.START_DATE AS S2_START, s2.END_DATE AS S2_END
    FROM SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS a1
    JOIN SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS a2 
        ON a1.DEVICE_ID = a2.DEVICE_ID AND a1.ASSIGNMENT_ID < a2.ASSIGNMENT_ID
    JOIN SCRATCH.HARDWARE_TRACKER.DEVICES d ON a1.DEVICE_ID = d.DEVICE_ID
    JOIN SCRATCH.HARDWARE_TRACKER.STUDIES s1 ON a1.STUDY_ID = s1.STUDY_ID
    JOIN SCRATCH.HARDWARE_TRACKER.STUDIES s2 ON a2.STUDY_ID = s2.STUDY_ID
    WHERE a1.STATUS IN ('ACTIVE', 'RESERVED') AND a2.STATUS IN ('ACTIVE', 'RESERVED')
    AND s1.START_DATE <= COALESCE(s2.END_DATE, '2099-12-31')
    AND s2.START_DATE <= COALESCE(s1.END_DATE, '2099-12-31')
    ORDER BY d.SERIAL_NUMBER
""").to_pandas()

if len(conflicts) > 0:
    st.warning(f"Found {len(conflicts)} scheduling conflicts!")
    st.dataframe(conflicts, use_container_width=True, hide_index=True)
else:
    st.success("No scheduling conflicts detected.")

# --- Availability Forecast ---
st.header("Availability Forecast")
st.markdown("Projected device availability based on study end dates.")

forecast = session.sql("""
    SELECT 
        s.END_DATE,
        s.STUDY_NAME,
        COUNT(a.DEVICE_ID) AS DEVICES_RETURNING,
        d_types.DEVICE_TYPES
    FROM SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS a
    JOIN SCRATCH.HARDWARE_TRACKER.STUDIES s ON a.STUDY_ID = s.STUDY_ID
    JOIN (
        SELECT a2.STUDY_ID, LISTAGG(DISTINCT d.DEVICE_TYPE, ', ') AS DEVICE_TYPES
        FROM SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS a2
        JOIN SCRATCH.HARDWARE_TRACKER.DEVICES d ON a2.DEVICE_ID = d.DEVICE_ID
        WHERE a2.STATUS IN ('ACTIVE', 'RESERVED')
        GROUP BY a2.STUDY_ID
    ) d_types ON d_types.STUDY_ID = s.STUDY_ID
    WHERE a.STATUS IN ('ACTIVE', 'RESERVED') AND s.END_DATE IS NOT NULL AND s.END_DATE >= CURRENT_DATE()
    GROUP BY s.END_DATE, s.STUDY_NAME, d_types.DEVICE_TYPES
    ORDER BY s.END_DATE
""").to_pandas()

if len(forecast) > 0:
    st.dataframe(forecast, use_container_width=True, hide_index=True)
else:
    st.info("No upcoming device returns scheduled (no active studies with end dates).")
