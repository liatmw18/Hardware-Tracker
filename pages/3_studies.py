import streamlit as st
import pandas as pd
from snowflake.snowpark.context import get_active_session
import sys
sys.path.insert(0, "/")
from helpers.dropdowns import editable_dropdown, get_field_options

st.set_page_config(page_title="Studies", layout="wide")
st.title("Study Management")

session = get_active_session()

# --- Auto-sync DCA campaigns (last 12 months) on page load ---
session.sql("""
    MERGE INTO SCRATCH.HARDWARE_TRACKER.STUDIES AS target
    USING (
        SELECT 
            CAMPAIGN_ID,
            CAMPAIGN_ID AS STUDY_NAME,
            OWNER AS PRINCIPAL_INVESTIGATOR,
            TRY_TO_DATE(REGEXP_SUBSTR(DURING::TEXT, '\\\\d{4}-\\\\d{2}-\\\\d{2}', 1, 1)) AS START_DATE,
            TRY_TO_DATE(REGEXP_SUBSTR(DURING::TEXT, '\\\\d{4}-\\\\d{2}-\\\\d{2}', 1, 2)) AS END_DATE,
            TARGET_COLLECTIONS AS PARTICIPANT_COUNT,
            CASE 
                WHEN UPPER(DURING::TEXT) LIKE '%,)%' THEN 'ACTIVE'
                WHEN TRY_TO_DATE(REGEXP_SUBSTR(DURING::TEXT, '\\\\d{4}-\\\\d{2}-\\\\d{2}', 1, 2)) < CURRENT_DATE() THEN 'COMPLETED'
                WHEN TRY_TO_DATE(REGEXP_SUBSTR(DURING::TEXT, '\\\\d{4}-\\\\d{2}-\\\\d{2}', 1, 1)) <= CURRENT_DATE() THEN 'ACTIVE'
                ELSE 'PLANNED'
            END AS STATUS
        FROM POSTGRES_PROD.DCA.CAMPAIGNS
        WHERE CREATED_AT >= DATEADD('month', -12, CURRENT_TIMESTAMP())
          AND DELETED = FALSE
    ) AS source
    ON target.DCA_CAMPAIGN_ID = source.CAMPAIGN_ID
    WHEN NOT MATCHED THEN INSERT 
        (STUDY_NAME, PRINCIPAL_INVESTIGATOR, START_DATE, END_DATE, PARTICIPANT_COUNT, STATUS, DCA_CAMPAIGN_ID)
    VALUES 
        (source.STUDY_NAME, source.PRINCIPAL_INVESTIGATOR, source.START_DATE, source.END_DATE, 
         source.PARTICIPANT_COUNT, source.STATUS, source.CAMPAIGN_ID)
""").collect()

# --- Auto-sync device study assignments from DCA ---
session.sql("""
    MERGE INTO SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS AS target
    USING (
        SELECT 
            d.DEVICE_ID,
            s.STUDY_ID,
            MIN(m.STUDY_DATE) AS ASSIGNED_DATE,
            MAX(m.STUDY_DATE) AS LAST_DATE,
            CASE 
                WHEN MAX(m.STUDY_DATE) < CURRENT_DATE() THEN 'RETURNED'
                ELSE 'ACTIVE'
            END AS STATUS
        FROM SCRATCH.HARDWARE_TRACKER.DEVICES d
        JOIN POSTGRES_PROD.RND_DCA.CAMPAIGN_STUDY_METADATA_DCA m 
            ON d.SERIAL_NUMBER = m.STRAP_ID
        JOIN SCRATCH.HARDWARE_TRACKER.STUDIES s 
            ON s.DCA_CAMPAIGN_ID = m.CAMPAIGN_NAME
        WHERE m.STUDY_DATE >= DATEADD('month', -12, CURRENT_DATE())
        GROUP BY d.DEVICE_ID, s.STUDY_ID
    ) AS source
    ON target.DEVICE_ID = source.DEVICE_ID AND target.STUDY_ID = source.STUDY_ID
    WHEN NOT MATCHED THEN INSERT 
        (DEVICE_ID, STUDY_ID, ASSIGNED_DATE, RETURNED_DATE, STATUS)
    VALUES 
        (source.DEVICE_ID, source.STUDY_ID, source.ASSIGNED_DATE, 
         CASE WHEN source.STATUS = 'RETURNED' THEN source.LAST_DATE ELSE NULL END,
         source.STATUS)
    WHEN MATCHED THEN UPDATE SET
        ASSIGNED_DATE = source.ASSIGNED_DATE,
        RETURNED_DATE = CASE WHEN source.STATUS = 'RETURNED' THEN source.LAST_DATE ELSE NULL END,
        STATUS = source.STATUS
""").collect()

# --- Study list ---
studies_df = session.sql("""
    SELECT s.*, 
        (SELECT COUNT(*) FROM SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS a 
         WHERE a.STUDY_ID = s.STUDY_ID AND a.STATUS IN ('ACTIVE', 'RESERVED')) AS DEVICE_COUNT
    FROM SCRATCH.HARDWARE_TRACKER.STUDIES s
    ORDER BY s.START_DATE DESC NULLS LAST
""").to_pandas()

# --- Tabs ---
tab_list, tab_create, tab_assign = st.tabs(["Studies List", "Create Study", "Assign Devices"])

with tab_list:
    status_filter = st.selectbox("Filter by status", ["All", "PLANNED", "ACTIVE", "COMPLETED", "CANCELLED"])
    filtered = studies_df if status_filter == "All" else studies_df[studies_df["STATUS"] == status_filter]

    if len(filtered) > 0:
        display_cols = ["STUDY_NAME", "PRINCIPAL_INVESTIGATOR", "START_DATE", "END_DATE",
                        "PARTICIPANT_COUNT", "STATUS", "DEVICE_COUNT"]
        if "DCA_CAMPAIGN_ID" in filtered.columns:
            display_cols.append("DCA_CAMPAIGN_ID")
        st.dataframe(
            filtered[display_cols],
            use_container_width=True, hide_index=True
        )

        # Study detail
        selected_study = st.selectbox("Select study for details", filtered["STUDY_NAME"].tolist())
        if selected_study:
            study_row = filtered[filtered["STUDY_NAME"] == selected_study].iloc[0]
            study_id = study_row["STUDY_ID"]
            dca_campaign_id = study_row.get("DCA_CAMPAIGN_ID", "") or ""

            st.subheader(f"Devices in: {selected_study}")

            # Get assignment roster
            roster = session.sql(f"""
                SELECT d.SERIAL_NUMBER, d.DEVICE_TYPE, d.VARIANT, d.CONDITION_STATUS,
                       a.STATUS AS ASSIGNMENT_STATUS, a.ASSIGNED_DATE, a.RETURNED_DATE
                FROM SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS a
                JOIN SCRATCH.HARDWARE_TRACKER.DEVICES d ON a.DEVICE_ID = d.DEVICE_ID
                WHERE a.STUDY_ID = '{study_id}'
                ORDER BY a.ASSIGNED_DATE DESC
            """).to_pandas()

            if len(roster) > 0:
                # Split into used vs planned
                used = roster[roster["ASSIGNMENT_STATUS"].isin(["ACTIVE", "RETURNED"])]
                planned = roster[roster["ASSIGNMENT_STATUS"] == "RESERVED"]

                if len(used) > 0:
                    st.markdown("**Devices used in this study:**")
                    st.dataframe(used, use_container_width=True, hide_index=True)

                if len(planned) > 0:
                    st.markdown("**Planned/Reserved devices:**")
                    st.dataframe(planned, use_container_width=True, hide_index=True)

                # Detailed session-level usage from DCA (when each device was actually used)
                if dca_campaign_id:
                    with st.expander("Detailed usage dates (from DCA)"):
                        dca_detail = session.sql(f"""
                            SELECT m.STRAP_ID AS SERIAL_NUMBER, m.STUDY_DATE, 
                                   m.HARDWARE_VARIANT, m.POSITION_ID, m.SIZE
                            FROM POSTGRES_PROD.RND_DCA.CAMPAIGN_STUDY_METADATA_DCA m
                            WHERE m.CAMPAIGN_NAME = '{dca_campaign_id}'
                              AND m.STRAP_ID IN (SELECT SERIAL_NUMBER FROM SCRATCH.HARDWARE_TRACKER.DEVICES)
                            ORDER BY m.STRAP_ID, m.STUDY_DATE
                        """).to_pandas()
                        if len(dca_detail) > 0:
                            st.dataframe(dca_detail, use_container_width=True, hide_index=True)
                        else:
                            st.info("No detailed DCA session data found for tracked devices.")

                # Return devices
                active_devices = roster[roster["ASSIGNMENT_STATUS"].isin(["ACTIVE", "RESERVED"])]
                if len(active_devices) > 0:
                    return_serials = st.multiselect("Select devices to return",
                                                    active_devices["SERIAL_NUMBER"].tolist())
                    if st.button("Return Selected Devices") and return_serials:
                        for serial in return_serials:
                            session.sql(f"""
                                UPDATE SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS SET
                                    STATUS = 'RETURNED', RETURNED_DATE = CURRENT_DATE()
                                WHERE STUDY_ID = '{study_id}' 
                                AND DEVICE_ID = (SELECT DEVICE_ID FROM SCRATCH.HARDWARE_TRACKER.DEVICES WHERE SERIAL_NUMBER = '{serial}')
                                AND STATUS IN ('ACTIVE', 'RESERVED')
                            """).collect()
                        st.success(f"Returned {len(return_serials)} devices.")
                        st.rerun()
            else:
                st.info("No devices assigned to this study yet.")

            # Edit study
            with st.expander("Edit Study"):
                with st.form(f"edit_study_{study_id}"):
                    edit_pi = editable_dropdown("Principal Investigator", "principal_investigator", f"es_pi_{study_id}", current_value=study_row.get("PRINCIPAL_INVESTIGATOR", ""))
                    edit_status = editable_dropdown("Status", "study_status", f"es_status_{study_id}", current_value=study_row.get("STATUS", ""))
                    edit_start = st.date_input("Start Date", value=study_row["START_DATE"] if pd.notna(study_row["START_DATE"]) else None)
                    edit_end = st.date_input("End Date", value=study_row["END_DATE"] if pd.notna(study_row["END_DATE"]) else None)
                    edit_participants = st.number_input("Participant Count", value=int(study_row.get("PARTICIPANT_COUNT", 0) or 0))
                    edit_desc = st.text_area("Description", value=study_row.get("DESCRIPTION", "") or "")
                    if st.form_submit_button("Save"):
                        session.sql(f"""
                            UPDATE SCRATCH.HARDWARE_TRACKER.STUDIES SET
                                PRINCIPAL_INVESTIGATOR = '{edit_pi}',
                                STATUS = '{edit_status}',
                                START_DATE = '{edit_start}',
                                END_DATE = '{edit_end}',
                                PARTICIPANT_COUNT = {edit_participants},
                                DESCRIPTION = '{edit_desc.replace("'", "''")}'
                            WHERE STUDY_ID = '{study_id}'
                        """).collect()
                        st.success("Study updated!")
                        st.rerun()
    else:
        st.info("No studies found. Create one in the 'Create Study' tab.")

with tab_create:
    st.subheader("Create New Study")
    with st.form("create_study"):
        study_name = st.text_input("Study Name *")
        col1, col2 = st.columns(2)
        with col1:
            pi = editable_dropdown("Principal Investigator", "principal_investigator", "cs_pi")
            start = st.date_input("Start Date", value=None)
            participants = st.number_input("Participant Count", min_value=0, value=0)
        with col2:
            status = editable_dropdown("Status", "study_status", "cs_status")
            end = st.date_input("End Date", value=None)
        description = st.text_area("Description")
        if st.form_submit_button("Create Study"):
            if study_name:
                start_val = f"'{start}'" if start else "NULL"
                end_val = f"'{end}'" if end else "NULL"
                session.sql(f"""
                    INSERT INTO SCRATCH.HARDWARE_TRACKER.STUDIES 
                    (STUDY_NAME, PRINCIPAL_INVESTIGATOR, START_DATE, END_DATE, PARTICIPANT_COUNT, STATUS, DESCRIPTION)
                    VALUES ('{study_name}', '{pi}', {start_val}, {end_val}, {participants}, '{status}', '{description.replace("'", "''")}')
                """).collect()
                st.success(f"Study '{study_name}' created!")
                st.rerun()
            else:
                st.error("Study Name is required.")

with tab_assign:
    st.subheader("Assign Devices to Study")
    if len(studies_df) > 0:
        target_study = st.selectbox("Select study", studies_df["STUDY_NAME"].tolist(), key="assign_study")
        target_study_id = studies_df[studies_df["STUDY_NAME"] == target_study].iloc[0]["STUDY_ID"]

        # Available devices (not currently assigned to an active study)
        available = session.sql("""
            SELECT d.SERIAL_NUMBER, d.DEVICE_TYPE, d.VARIANT, d.CONDITION_STATUS, d.CURRENT_LOCATION
            FROM SCRATCH.HARDWARE_TRACKER.DEVICES d
            WHERE d.DEVICE_ID NOT IN (
                SELECT DEVICE_ID FROM SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS 
                WHERE STATUS IN ('ACTIVE', 'RESERVED')
            )
            ORDER BY d.DEVICE_TYPE, d.VARIANT
        """).to_pandas()

        st.markdown(f"**Available devices:** {len(available)}")

        # Filter available
        avail_type = st.selectbox("Filter by type", ["All"] + available["DEVICE_TYPE"].unique().tolist(), key="avail_type_filter")
        if avail_type != "All":
            available = available[available["DEVICE_TYPE"] == avail_type]

        if len(available) > 0:
            selected_devices = st.multiselect("Select devices to assign", available["SERIAL_NUMBER"].tolist())
            assign_status = st.radio("Assignment type", ["ACTIVE", "RESERVED"], horizontal=True)
            if st.button("Assign Selected") and selected_devices:
                for serial in selected_devices:
                    session.sql(f"""
                        INSERT INTO SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS 
                        (DEVICE_ID, STUDY_ID, STATUS)
                        SELECT DEVICE_ID, '{target_study_id}', '{assign_status}'
                        FROM SCRATCH.HARDWARE_TRACKER.DEVICES WHERE SERIAL_NUMBER = '{serial}'
                    """).collect()
                st.success(f"Assigned {len(selected_devices)} devices to {target_study}!")
                st.rerun()
        else:
            st.info("No available devices matching the filter.")
    else:
        st.info("Create a study first.")
