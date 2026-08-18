import streamlit as st
import pandas as pd
import snowflake.connector

st.set_page_config(page_title="Whoop Labs Hardware Tracker", layout="wide")

# --- Authentication & Connection ---
ALLOWED_USERS = [
    "liat.mayer@whoop.com",
    # Add your team members here:
    # "teammate1@whoop.com",
    # "teammate2@whoop.com",
]


@st.cache_resource
def get_snowflake_connection():
    return snowflake.connector.connect(
        account=st.secrets["snowflake"]["account"],
        user=st.secrets["snowflake"]["user"],
        password=st.secrets["snowflake"]["password"],
        role=st.secrets["snowflake"]["role"],
        warehouse=st.secrets["snowflake"]["warehouse"],
        database=st.secrets["snowflake"]["database"],
        schema=st.secrets["snowflake"]["schema"],
    )


def get_connection():
    return get_snowflake_connection()


def run_query(query):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("USE WAREHOUSE WHOOP_LABS_WH")
    cur.execute(query)
    columns = [desc[0] for desc in cur.description] if cur.description else []
    rows = cur.fetchall()
    cur.close()
    if columns:
        return pd.DataFrame(rows, columns=columns)
    return pd.DataFrame()


def run_command(query):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("USE WAREHOUSE WHOOP_LABS_WH")
    cur.execute(query)
    cur.close()


def login_form():
    st.title("Whoop Labs Hardware Tracker")
    st.markdown("Enter your Whoop email to access the tracker.")
    with st.form("login"):
        email = st.text_input("Your Whoop email")
        submitted = st.form_submit_button("Sign In")
        if submitted:
            email_lower = email.strip().lower()
            if email_lower in [u.lower() for u in ALLOWED_USERS]:
                st.session_state["user"] = email_lower
                st.rerun()
            elif email_lower.endswith("@whoop.com"):
                st.session_state["user"] = email_lower
                st.rerun()
            else:
                st.error("Access denied. Only @whoop.com emails are allowed.")


if "user" not in st.session_state:
    login_form()
    st.stop()

st.sidebar.markdown(f"Logged in as: **{st.session_state.get('user', '')}**")
if st.sidebar.button("Logout"):
    st.session_state.pop("user", None)
    st.rerun()


# --- Helper Functions ---
def get_field_options(field_name: str) -> list:
    df = run_query(
        f"SELECT OPTION_VALUE FROM SCRATCH.HARDWARE_TRACKER.FIELD_OPTIONS "
        f"WHERE FIELD_NAME = '{field_name}' ORDER BY SORT_ORDER, OPTION_VALUE"
    )
    return df["OPTION_VALUE"].tolist() if len(df) > 0 else []


def add_field_option(field_name: str, option_value: str):
    run_command(
        f"INSERT INTO SCRATCH.HARDWARE_TRACKER.FIELD_OPTIONS (FIELD_NAME, OPTION_VALUE) "
        f"SELECT '{field_name}', '{option_value}' "
        f"WHERE NOT EXISTS (SELECT 1 FROM SCRATCH.HARDWARE_TRACKER.FIELD_OPTIONS "
        f"WHERE FIELD_NAME = '{field_name}' AND OPTION_VALUE = '{option_value}')"
    )


def editable_dropdown(label, field_name, key, current_value=None, allow_none=True):
    options = get_field_options(field_name)
    add_option = "+ Add new..."
    options_with_add = options + [add_option]
    if allow_none:
        options_with_add = [""] + options_with_add
    default_index = 0
    if current_value and current_value in options_with_add:
        default_index = options_with_add.index(current_value)
    selected = st.selectbox(label, options_with_add, index=default_index, key=key)
    if selected == add_option:
        new_value = st.text_input(f"New {label}:", key=f"{key}_new")
        if new_value:
            add_field_option(field_name, new_value)
            st.rerun()
        return current_value or ""
    return selected


# --- Navigation ---
page = st.sidebar.radio("Navigation", ["Inventory", "Scanner", "Studies", "Analytics", "Study Planner"])

# ===================== INVENTORY PAGE =====================
if page == "Inventory":
    st.title("Device Inventory")

    st.sidebar.header("Filters")
    filter_type = st.sidebar.selectbox("Device Type", ["All"] + get_field_options("device_type"))
    filter_status = st.sidebar.selectbox("Condition", ["All"] + get_field_options("condition_status"))
    filter_search = st.sidebar.text_input("Search (serial, location, holder)")

    query = "SELECT * FROM SCRATCH.HARDWARE_TRACKER.DEVICES WHERE 1=1"
    if filter_type != "All":
        query += f" AND DEVICE_TYPE = '{filter_type}'"
    if filter_status != "All":
        query += f" AND CONDITION_STATUS = '{filter_status}'"
    if filter_search:
        query += f" AND (SERIAL_NUMBER ILIKE '%{filter_search}%' OR CURRENT_LOCATION ILIKE '%{filter_search}%' OR CURRENT_HOLDER ILIKE '%{filter_search}%')"
    query += " ORDER BY UPDATED_AT DESC"

    devices_df = run_query(query)
    total = len(devices_df)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Devices", total)
    if total > 0:
        col2.metric("Good", len(devices_df[devices_df["CONDITION_STATUS"] == "Good"]))
        col3.metric("In Use", len(devices_df[devices_df["CURRENT_HOLDER"].notna() & (devices_df["CURRENT_HOLDER"] != "")]))
        col4.metric("Types", devices_df["DEVICE_TYPE"].nunique())

    st.subheader(f"Devices ({total})")
    if total > 0:
        display_cols = ["SERIAL_NUMBER", "DEVICE_TYPE", "VARIANT", "CONFIG", "CONDITION_STATUS",
                        "BATTERY_HEALTH", "CURRENT_LOCATION", "CURRENT_HOLDER", "FIRMWARE_VERSION", "NOTES"]
        st.dataframe(devices_df[display_cols], use_container_width=True, hide_index=True)
    else:
        st.info("No devices found. Add your first device below or use CSV import.")

    tab_add, tab_edit, tab_import = st.tabs(["Add Device", "Edit Device", "Bulk Import"])

    with tab_add:
        with st.form("add_device_form"):
            col_a, col_b = st.columns(2)
            with col_a:
                new_serial = st.text_input("Serial Number *")
                new_type = editable_dropdown("Device Type *", "device_type", "add_type")
                new_variant = editable_dropdown("Variant", "variant", "add_variant")
                new_config = editable_dropdown("Config", "config", "add_config")
            with col_b:
                new_firmware = editable_dropdown("Firmware Version", "firmware_version", "add_firmware")
                new_battery = editable_dropdown("Battery Health", "battery_health", "add_battery")
                new_condition = editable_dropdown("Condition", "condition_status", "add_condition")
                new_location = editable_dropdown("Location", "location", "add_location")
            new_holder = editable_dropdown("Current Holder", "holder", "add_holder")
            new_notes = st.text_area("Notes")
            submitted = st.form_submit_button("Add Device")
            if submitted and new_serial and new_type:
                run_command(f"""
                    INSERT INTO SCRATCH.HARDWARE_TRACKER.DEVICES 
                    (SERIAL_NUMBER, DEVICE_TYPE, VARIANT, CONFIG, FIRMWARE_VERSION, 
                     BATTERY_HEALTH, CONDITION_STATUS, CURRENT_LOCATION, CURRENT_HOLDER, NOTES)
                    VALUES ('{new_serial}', '{new_type}', '{new_variant}', '{new_config}', 
                            '{new_firmware}', '{new_battery}', '{new_condition}', '{new_location}', 
                            '{new_holder}', '{new_notes}')
                """)
                st.success(f"Device {new_serial} added!")
                st.rerun()
            elif submitted:
                st.error("Serial Number and Device Type are required.")

    with tab_edit:
        if total > 0:
            serial_options = devices_df["SERIAL_NUMBER"].tolist()
            selected_serial = st.selectbox("Select device to edit", serial_options, key="edit_select")
            device_row = devices_df[devices_df["SERIAL_NUMBER"] == selected_serial].iloc[0]
            with st.form("edit_device_form"):
                col_e1, col_e2 = st.columns(2)
                with col_e1:
                    st.text_input("Serial Number", value=device_row["SERIAL_NUMBER"], disabled=True)
                    edit_type = editable_dropdown("Device Type", "device_type", "edit_type", current_value=device_row.get("DEVICE_TYPE", ""))
                    edit_variant = editable_dropdown("Variant", "variant", "edit_variant", current_value=device_row.get("VARIANT", ""))
                    edit_config = editable_dropdown("Config", "config", "edit_config", current_value=device_row.get("CONFIG", ""))
                with col_e2:
                    edit_firmware = editable_dropdown("Firmware Version", "firmware_version", "edit_firmware", current_value=device_row.get("FIRMWARE_VERSION", ""))
                    edit_battery = editable_dropdown("Battery Health", "battery_health", "edit_battery", current_value=device_row.get("BATTERY_HEALTH", ""))
                    edit_condition = editable_dropdown("Condition", "condition_status", "edit_condition", current_value=device_row.get("CONDITION_STATUS", ""))
                    edit_location = editable_dropdown("Location", "location", "edit_location", current_value=device_row.get("CURRENT_LOCATION", ""))
                edit_holder = editable_dropdown("Current Holder", "holder", "edit_holder", current_value=device_row.get("CURRENT_HOLDER", ""))
                edit_notes = st.text_area("Notes", value=device_row.get("NOTES", "") or "")
                save = st.form_submit_button("Save Changes")
                if save:
                    device_id = device_row["DEVICE_ID"]
                    old_loc = device_row.get("CURRENT_LOCATION", "") or ""
                    old_holder = device_row.get("CURRENT_HOLDER", "") or ""
                    if edit_location != old_loc or edit_holder != old_holder:
                        run_command(f"""
                            INSERT INTO SCRATCH.HARDWARE_TRACKER.LOCATION_HISTORY 
                            (DEVICE_ID, LOCATION, HOLDER, ACTION, CHANGED_BY)
                            VALUES ('{device_id}', '{edit_location}', '{edit_holder}', 'REASSIGNED', CURRENT_USER())
                        """)
                    run_command(f"""
                        UPDATE SCRATCH.HARDWARE_TRACKER.DEVICES SET
                            DEVICE_TYPE = '{edit_type}', VARIANT = '{edit_variant}', CONFIG = '{edit_config}',
                            FIRMWARE_VERSION = '{edit_firmware}', BATTERY_HEALTH = '{edit_battery}',
                            CONDITION_STATUS = '{edit_condition}', CURRENT_LOCATION = '{edit_location}',
                            CURRENT_HOLDER = '{edit_holder}', NOTES = '{edit_notes}',
                            UPDATED_AT = CURRENT_TIMESTAMP()
                        WHERE DEVICE_ID = '{device_id}'
                    """)
                    st.success("Device updated!")
                    st.rerun()
        else:
            st.info("No devices to edit.")

    with tab_import:
        st.markdown("Upload CSV with columns: `SERIAL_NUMBER, DEVICE_TYPE, VARIANT, CONFIG, FIRMWARE_VERSION, BATTERY_HEALTH, CONDITION_STATUS, CURRENT_LOCATION, CURRENT_HOLDER, NOTES`")
        uploaded = st.file_uploader("Choose CSV file", type=["csv"])
        if uploaded:
            import_df = pd.read_csv(uploaded)
            st.dataframe(import_df.head(10))
            if st.button("Import All Rows"):
                for _, row in import_df.iterrows():
                    vals = {col: str(row.get(col, "") or "").replace("'", "''") for col in
                            ["SERIAL_NUMBER", "DEVICE_TYPE", "VARIANT", "CONFIG", "FIRMWARE_VERSION",
                             "BATTERY_HEALTH", "CONDITION_STATUS", "CURRENT_LOCATION", "CURRENT_HOLDER", "NOTES"]}
                    run_command(f"""
                        INSERT INTO SCRATCH.HARDWARE_TRACKER.DEVICES 
                        (SERIAL_NUMBER, DEVICE_TYPE, VARIANT, CONFIG, FIRMWARE_VERSION,
                         BATTERY_HEALTH, CONDITION_STATUS, CURRENT_LOCATION, CURRENT_HOLDER, NOTES)
                        VALUES ('{vals["SERIAL_NUMBER"]}', '{vals["DEVICE_TYPE"]}', '{vals["VARIANT"]}',
                                '{vals["CONFIG"]}', '{vals["FIRMWARE_VERSION"]}', '{vals["BATTERY_HEALTH"]}',
                                '{vals["CONDITION_STATUS"]}', '{vals["CURRENT_LOCATION"]}',
                                '{vals["CURRENT_HOLDER"]}', '{vals["NOTES"]}')
                    """)
                st.success(f"Imported {len(import_df)} devices!")
                st.rerun()

# ===================== SCANNER PAGE =====================
elif page == "Scanner":
    st.title("Device Scanner")
    st.markdown("Look up a device by scanning its QR code (USB scanner) or entering the serial number.")

    serial_input = st.text_input("Enter or scan serial number:", key="scanner_input", placeholder="Scan or type serial number...")

    if serial_input:
        results = run_query(f"""
            SELECT * FROM SCRATCH.HARDWARE_TRACKER.DEVICES 
            WHERE SERIAL_NUMBER = '{serial_input}' OR SERIAL_NUMBER ILIKE '%{serial_input}%'
            LIMIT 5
        """)

        if len(results) == 0:
            st.warning(f"No device found matching '{serial_input}'")
        else:
            for idx, device in results.iterrows():
                st.divider()
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.markdown(f"**Serial:** {device['SERIAL_NUMBER']}")
                    st.markdown(f"**Type:** {device['DEVICE_TYPE']}")
                    st.markdown(f"**Variant:** {device.get('VARIANT', 'N/A')}")
                    st.markdown(f"**Config:** {device.get('CONFIG', 'N/A')}")
                with col2:
                    st.markdown(f"**Condition:** {device.get('CONDITION_STATUS', 'N/A')}")
                    st.markdown(f"**Battery:** {device.get('BATTERY_HEALTH', 'N/A')}")
                    st.markdown(f"**Firmware:** {device.get('FIRMWARE_VERSION', 'N/A')}")
                with col3:
                    st.markdown(f"**Location:** {device.get('CURRENT_LOCATION', 'N/A')}")
                    st.markdown(f"**Holder:** {device.get('CURRENT_HOLDER', 'N/A')}")

                assignments = run_query(f"""
                    SELECT s.STUDY_NAME, a.STATUS, a.ASSIGNED_DATE
                    FROM SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS a
                    JOIN SCRATCH.HARDWARE_TRACKER.STUDIES s ON a.STUDY_ID = s.STUDY_ID
                    WHERE a.DEVICE_ID = '{device['DEVICE_ID']}' AND a.STATUS IN ('ACTIVE', 'RESERVED')
                """)
                if len(assignments) > 0:
                    st.markdown("**Current Assignments:**")
                    st.dataframe(assignments, use_container_width=True, hide_index=True)
                else:
                    st.markdown("**Status:** Available (not assigned)")

                st.markdown("**Quick Actions:**")
                qcol1, qcol2, qcol3 = st.columns(3)
                with qcol1:
                    new_loc = editable_dropdown("Move to location", "location", f"scan_loc_{idx}")
                with qcol2:
                    new_holder = editable_dropdown("Assign to person", "holder", f"scan_holder_{idx}")
                with qcol3:
                    if st.button("Update Location", key=f"scan_update_{idx}"):
                        device_id = device["DEVICE_ID"]
                        run_command(f"""
                            UPDATE SCRATCH.HARDWARE_TRACKER.DEVICES SET
                                CURRENT_LOCATION = '{new_loc}', CURRENT_HOLDER = '{new_holder}',
                                UPDATED_AT = CURRENT_TIMESTAMP()
                            WHERE DEVICE_ID = '{device_id}'
                        """)
                        run_command(f"""
                            INSERT INTO SCRATCH.HARDWARE_TRACKER.LOCATION_HISTORY 
                            (DEVICE_ID, LOCATION, HOLDER, ACTION, CHANGED_BY)
                            VALUES ('{device_id}', '{new_loc}', '{new_holder}', 'SCANNED_UPDATE', CURRENT_USER())
                        """)
                        st.success("Location updated!")
                        st.rerun()

                with st.expander("Location History"):
                    history = run_query(f"""
                        SELECT LOCATION, HOLDER, ACTION, TIMESTAMP_VAL, CHANGED_BY
                        FROM SCRATCH.HARDWARE_TRACKER.LOCATION_HISTORY
                        WHERE DEVICE_ID = '{device['DEVICE_ID']}'
                        ORDER BY TIMESTAMP_VAL DESC LIMIT 10
                    """)
                    if len(history) > 0:
                        st.dataframe(history, use_container_width=True, hide_index=True)
                    else:
                        st.info("No location history yet.")

# ===================== STUDIES PAGE =====================
elif page == "Studies":
    st.title("Study Management")

    studies_df = run_query("""
        SELECT s.*, 
            (SELECT COUNT(*) FROM SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS a 
             WHERE a.STUDY_ID = s.STUDY_ID AND a.STATUS IN ('ACTIVE', 'RESERVED')) AS DEVICE_COUNT
        FROM SCRATCH.HARDWARE_TRACKER.STUDIES s
        ORDER BY s.START_DATE DESC NULLS LAST
    """)

    tab_list, tab_create, tab_assign = st.tabs(["Studies List", "Create Study", "Assign Devices"])

    with tab_list:
        status_filter = st.selectbox("Filter by status", ["All", "PLANNED", "ACTIVE", "COMPLETED", "CANCELLED"])
        filtered = studies_df if status_filter == "All" else studies_df[studies_df["STATUS"] == status_filter]

        if len(filtered) > 0:
            st.dataframe(
                filtered[["STUDY_NAME", "PRINCIPAL_INVESTIGATOR", "START_DATE", "END_DATE",
                          "PARTICIPANT_COUNT", "STATUS", "DEVICE_COUNT", "DESCRIPTION"]],
                use_container_width=True, hide_index=True
            )

            selected_study = st.selectbox("Select study for details", filtered["STUDY_NAME"].tolist())
            if selected_study:
                study_row = filtered[filtered["STUDY_NAME"] == selected_study].iloc[0]
                study_id = study_row["STUDY_ID"]

                st.subheader(f"Devices in: {selected_study}")
                roster = run_query(f"""
                    SELECT d.SERIAL_NUMBER, d.DEVICE_TYPE, d.VARIANT, d.CONDITION_STATUS,
                           a.STATUS AS ASSIGNMENT_STATUS, a.ASSIGNED_DATE, a.RETURNED_DATE
                    FROM SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS a
                    JOIN SCRATCH.HARDWARE_TRACKER.DEVICES d ON a.DEVICE_ID = d.DEVICE_ID
                    WHERE a.STUDY_ID = '{study_id}'
                    ORDER BY a.ASSIGNED_DATE DESC
                """)

                if len(roster) > 0:
                    st.dataframe(roster, use_container_width=True, hide_index=True)
                    active_devices = roster[roster["ASSIGNMENT_STATUS"].isin(["ACTIVE", "RESERVED"])]
                    if len(active_devices) > 0:
                        return_serials = st.multiselect("Select devices to return", active_devices["SERIAL_NUMBER"].tolist())
                        if st.button("Return Selected Devices") and return_serials:
                            for serial in return_serials:
                                run_command(f"""
                                    UPDATE SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS SET
                                        STATUS = 'RETURNED', RETURNED_DATE = CURRENT_DATE()
                                    WHERE STUDY_ID = '{study_id}' 
                                    AND DEVICE_ID = (SELECT DEVICE_ID FROM SCRATCH.HARDWARE_TRACKER.DEVICES WHERE SERIAL_NUMBER = '{serial}')
                                    AND STATUS IN ('ACTIVE', 'RESERVED')
                                """)
                            st.success(f"Returned {len(return_serials)} devices.")
                            st.rerun()
                else:
                    st.info("No devices assigned to this study yet.")
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
                    run_command(f"""
                        INSERT INTO SCRATCH.HARDWARE_TRACKER.STUDIES 
                        (STUDY_NAME, PRINCIPAL_INVESTIGATOR, START_DATE, END_DATE, PARTICIPANT_COUNT, STATUS, DESCRIPTION)
                        VALUES ('{study_name}', '{pi}', {start_val}, {end_val}, {participants}, '{status}', '{description.replace("'", "''")}')
                    """)
                    st.success(f"Study '{study_name}' created!")
                    st.rerun()
                else:
                    st.error("Study Name is required.")

    with tab_assign:
        st.subheader("Assign Devices to Study")
        if len(studies_df) > 0:
            target_study = st.selectbox("Select study", studies_df["STUDY_NAME"].tolist(), key="assign_study")
            target_study_id = studies_df[studies_df["STUDY_NAME"] == target_study].iloc[0]["STUDY_ID"]

            available = run_query("""
                SELECT d.SERIAL_NUMBER, d.DEVICE_TYPE, d.VARIANT, d.CONDITION_STATUS, d.CURRENT_LOCATION
                FROM SCRATCH.HARDWARE_TRACKER.DEVICES d
                WHERE d.DEVICE_ID NOT IN (
                    SELECT DEVICE_ID FROM SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS 
                    WHERE STATUS IN ('ACTIVE', 'RESERVED')
                )
                ORDER BY d.DEVICE_TYPE, d.VARIANT
            """)

            st.markdown(f"**Available devices:** {len(available)}")
            avail_type = st.selectbox("Filter by type", ["All"] + (available["DEVICE_TYPE"].unique().tolist() if len(available) > 0 else []), key="avail_type_filter")
            if avail_type != "All":
                available = available[available["DEVICE_TYPE"] == avail_type]

            if len(available) > 0:
                selected_devices = st.multiselect("Select devices to assign", available["SERIAL_NUMBER"].tolist())
                assign_status = st.radio("Assignment type", ["ACTIVE", "RESERVED"], horizontal=True)
                if st.button("Assign Selected") and selected_devices:
                    for serial in selected_devices:
                        run_command(f"""
                            INSERT INTO SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS 
                            (DEVICE_ID, STUDY_ID, STATUS)
                            SELECT DEVICE_ID, '{target_study_id}', '{assign_status}'
                            FROM SCRATCH.HARDWARE_TRACKER.DEVICES WHERE SERIAL_NUMBER = '{serial}'
                        """)
                    st.success(f"Assigned {len(selected_devices)} devices to {target_study}!")
                    st.rerun()
            else:
                st.info("No available devices matching the filter.")
        else:
            st.info("Create a study first.")

# ===================== ANALYTICS PAGE =====================
elif page == "Analytics":
    st.title("Hardware Analytics")
    devices_df = run_query("SELECT * FROM SCRATCH.HARDWARE_TRACKER.DEVICES")
    total = len(devices_df)

    if total == 0:
        st.info("No devices in the system yet.")
        st.stop()

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total Devices", total)
    in_use = len(devices_df[devices_df["CURRENT_HOLDER"].notna() & (devices_df["CURRENT_HOLDER"] != "")])
    col2.metric("In Use", in_use)
    col3.metric("Available", total - in_use)
    col4.metric("Device Types", devices_df["DEVICE_TYPE"].nunique())
    col5.metric("Needs Repair", len(devices_df[devices_df["CONDITION_STATUS"] == "Needs Repair"]))

    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("By Type")
        type_counts = devices_df["DEVICE_TYPE"].value_counts().reset_index()
        type_counts.columns = ["DEVICE_TYPE", "COUNT"]
        st.bar_chart(type_counts, x="DEVICE_TYPE", y="COUNT")
    with col_b:
        st.subheader("By Condition")
        cond_counts = devices_df["CONDITION_STATUS"].value_counts().reset_index()
        cond_counts.columns = ["CONDITION_STATUS", "COUNT"]
        st.bar_chart(cond_counts, x="CONDITION_STATUS", y="COUNT")

    if devices_df["BATTERY_HEALTH"].notna().any():
        st.subheader("Battery Health Distribution")
        batt_counts = devices_df["BATTERY_HEALTH"].value_counts().reset_index()
        batt_counts.columns = ["BATTERY_HEALTH", "COUNT"]
        st.bar_chart(batt_counts, x="BATTERY_HEALTH", y="COUNT")

    st.subheader("Study Utilization")
    assignments_df = run_query("""
        SELECT d.SERIAL_NUMBER, d.DEVICE_TYPE, d.VARIANT, s.STUDY_NAME, 
               a.ASSIGNED_DATE, a.RETURNED_DATE, a.STATUS,
               DATEDIFF('day', a.ASSIGNED_DATE, COALESCE(a.RETURNED_DATE, CURRENT_DATE())) AS DAYS_IN_STUDY
        FROM SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS a
        JOIN SCRATCH.HARDWARE_TRACKER.DEVICES d ON a.DEVICE_ID = d.DEVICE_ID
        JOIN SCRATCH.HARDWARE_TRACKER.STUDIES s ON a.STUDY_ID = s.STUDY_ID
        ORDER BY a.ASSIGNED_DATE DESC
    """)

    if len(assignments_df) > 0:
        col_u1, col_u2, col_u3 = st.columns(3)
        col_u1.metric("Total Assignments", len(assignments_df))
        col_u2.metric("Avg Days/Assignment", f"{assignments_df['DAYS_IN_STUDY'].mean():.0f}")
        col_u3.metric("Active Assignments", len(assignments_df[assignments_df["STATUS"].isin(["ACTIVE", "RESERVED"])]))

        study_counts = assignments_df.groupby("STUDY_NAME").size().reset_index(name="DEVICE_COUNT")
        st.bar_chart(study_counts, x="STUDY_NAME", y="DEVICE_COUNT")
    else:
        st.info("No study assignments recorded yet.")

    st.subheader("Device History Drill-Down")
    selected = st.selectbox("Select device", devices_df["SERIAL_NUMBER"].tolist())
    if selected:
        device = devices_df[devices_df["SERIAL_NUMBER"] == selected].iloc[0]
        device_id = device["DEVICE_ID"]
        st.markdown(f"**{device['DEVICE_TYPE']} {device.get('VARIANT', '')}** | Condition: {device.get('CONDITION_STATUS', 'N/A')} | Battery: {device.get('BATTERY_HEALTH', 'N/A')}")

        dcol1, dcol2 = st.columns(2)
        with dcol1:
            st.markdown("**Study History**")
            device_assignments = run_query(f"""
                SELECT s.STUDY_NAME, a.STATUS, a.ASSIGNED_DATE, a.RETURNED_DATE,
                       DATEDIFF('day', a.ASSIGNED_DATE, COALESCE(a.RETURNED_DATE, CURRENT_DATE())) AS DAYS
                FROM SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS a
                JOIN SCRATCH.HARDWARE_TRACKER.STUDIES s ON a.STUDY_ID = s.STUDY_ID
                WHERE a.DEVICE_ID = '{device_id}'
                ORDER BY a.ASSIGNED_DATE DESC
            """)
            if len(device_assignments) > 0:
                st.dataframe(device_assignments, use_container_width=True, hide_index=True)
            else:
                st.info("No study history.")
        with dcol2:
            st.markdown("**Location History**")
            loc_history = run_query(f"""
                SELECT LOCATION, HOLDER, ACTION, TIMESTAMP_VAL, CHANGED_BY
                FROM SCRATCH.HARDWARE_TRACKER.LOCATION_HISTORY
                WHERE DEVICE_ID = '{device_id}'
                ORDER BY TIMESTAMP_VAL DESC LIMIT 20
            """)
            if len(loc_history) > 0:
                st.dataframe(loc_history, use_container_width=True, hide_index=True)
            else:
                st.info("No location history.")

# ===================== STUDY PLANNER PAGE =====================
elif page == "Study Planner":
    st.title("Study Planner")
    st.markdown("Plan future studies by specifying device requirements. The system checks availability and detects conflicts.")

    st.header("Current Inventory Status")
    inventory_summary = run_query("""
        SELECT 
            d.DEVICE_TYPE, d.VARIANT, COUNT(*) AS TOTAL,
            SUM(CASE WHEN d.DEVICE_ID NOT IN (
                SELECT DEVICE_ID FROM SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS WHERE STATUS IN ('ACTIVE', 'RESERVED')
            ) THEN 1 ELSE 0 END) AS AVAILABLE,
            SUM(CASE WHEN d.DEVICE_ID IN (
                SELECT DEVICE_ID FROM SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS WHERE STATUS = 'ACTIVE'
            ) THEN 1 ELSE 0 END) AS IN_ACTIVE_STUDY,
            SUM(CASE WHEN d.DEVICE_ID IN (
                SELECT DEVICE_ID FROM SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS WHERE STATUS = 'RESERVED'
            ) THEN 1 ELSE 0 END) AS RESERVED
        FROM SCRATCH.HARDWARE_TRACKER.DEVICES d
        WHERE d.CONDITION_STATUS != 'Retired'
        GROUP BY d.DEVICE_TYPE, d.VARIANT
        ORDER BY d.DEVICE_TYPE, d.VARIANT
    """)
    if len(inventory_summary) > 0:
        st.dataframe(inventory_summary, use_container_width=True, hide_index=True)
    else:
        st.info("No devices in inventory.")

    st.header("Capacity Planning")
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

        check_start = st.date_input("Study start date", value=None, key="plan_start")
        check_end = st.date_input("Study end date", value=None, key="plan_end")

        if st.form_submit_button("Check Availability"):
            if not requirements:
                st.warning("Add at least one device requirement.")
            else:
                all_met = True
                for req in requirements:
                    variant_filter = f"AND d.VARIANT = '{req['variant']}'" if req["variant"] and req["variant"] != "Any" else ""
                    if check_start and check_end:
                        avail_q = f"""
                            SELECT COUNT(*) AS CNT FROM SCRATCH.HARDWARE_TRACKER.DEVICES d
                            WHERE d.DEVICE_TYPE = '{req['type']}' {variant_filter} AND d.CONDITION_STATUS != 'Retired'
                            AND d.DEVICE_ID NOT IN (
                                SELECT a.DEVICE_ID FROM SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS a
                                JOIN SCRATCH.HARDWARE_TRACKER.STUDIES s ON a.STUDY_ID = s.STUDY_ID
                                WHERE a.STATUS IN ('ACTIVE', 'RESERVED')
                                AND s.START_DATE <= '{check_end}' AND (s.END_DATE >= '{check_start}' OR s.END_DATE IS NULL)
                            )
                        """
                    else:
                        avail_q = f"""
                            SELECT COUNT(*) AS CNT FROM SCRATCH.HARDWARE_TRACKER.DEVICES d
                            WHERE d.DEVICE_TYPE = '{req['type']}' {variant_filter} AND d.CONDITION_STATUS != 'Retired'
                            AND d.DEVICE_ID NOT IN (
                                SELECT DEVICE_ID FROM SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS WHERE STATUS IN ('ACTIVE', 'RESERVED')
                            )
                        """
                    result = run_query(avail_q)
                    available_count = result.iloc[0]["CNT"]
                    variant_label = f" ({req['variant']})" if req["variant"] and req["variant"] != "Any" else ""
                    needed = req["count"]
                    if available_count >= needed:
                        st.success(f"{req['type']}{variant_label}: {available_count} available, {needed} needed")
                    else:
                        st.error(f"{req['type']}{variant_label}: {available_count} available, {needed} needed - SHORTFALL of {needed - available_count}")
                        all_met = False
                if all_met:
                    st.success("All requirements can be met with current inventory!")
                else:
                    st.warning("Some requirements cannot be met.")

    st.header("Conflict Detection")
    conflicts = run_query("""
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
    """)
    if len(conflicts) > 0:
        st.warning(f"Found {len(conflicts)} scheduling conflicts!")
        st.dataframe(conflicts, use_container_width=True, hide_index=True)
    else:
        st.success("No scheduling conflicts detected.")

    st.header("Availability Forecast")
    forecast = run_query("""
        SELECT s.END_DATE, s.STUDY_NAME, COUNT(a.DEVICE_ID) AS DEVICES_RETURNING
        FROM SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS a
        JOIN SCRATCH.HARDWARE_TRACKER.STUDIES s ON a.STUDY_ID = s.STUDY_ID
        WHERE a.STATUS IN ('ACTIVE', 'RESERVED') AND s.END_DATE IS NOT NULL AND s.END_DATE >= CURRENT_DATE()
        GROUP BY s.END_DATE, s.STUDY_NAME
        ORDER BY s.END_DATE
    """)
    if len(forecast) > 0:
        st.dataframe(forecast, use_container_width=True, hide_index=True)
    else:
        st.info("No upcoming device returns scheduled.")

