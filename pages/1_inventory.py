import streamlit as st
import pandas as pd
from snowflake.snowpark.context import get_active_session
import sys
sys.path.insert(0, "/")
from helpers.dropdowns import editable_dropdown, get_session

st.set_page_config(page_title="Device Inventory", layout="wide")
st.title("Device Inventory")

session = get_session()

# --- Auto-sync study assignments from DCA on page load ---
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

# --- Filters ---
st.sidebar.header("Filters")
filter_type = st.sidebar.selectbox("Device Type", ["All"] + [r["OPTION_VALUE"] for r in session.sql(
    "SELECT OPTION_VALUE FROM SCRATCH.HARDWARE_TRACKER.FIELD_OPTIONS WHERE FIELD_NAME='device_type' ORDER BY SORT_ORDER"
).collect()])
filter_status = st.sidebar.selectbox("Condition", ["All"] + [r["OPTION_VALUE"] for r in session.sql(
    "SELECT OPTION_VALUE FROM SCRATCH.HARDWARE_TRACKER.FIELD_OPTIONS WHERE FIELD_NAME='condition_status' ORDER BY SORT_ORDER"
).collect()])
all_serials = [r["SERIAL_NUMBER"] for r in session.sql(
    "SELECT SERIAL_NUMBER FROM SCRATCH.HARDWARE_TRACKER.DEVICES ORDER BY SERIAL_NUMBER"
).collect()]
filter_serial = st.sidebar.selectbox("Search by Serial Number", [None] + all_serials, index=0, format_func=lambda x: "Start typing..." if x is None else x)

# --- Build query ---
query = "SELECT * FROM SCRATCH.HARDWARE_TRACKER.DEVICES WHERE 1=1"
if filter_type != "All":
    query += f" AND DEVICE_TYPE = '{filter_type}'"
if filter_status != "All":
    query += f" AND CONDITION_STATUS = '{filter_status}'"
if filter_serial:
    query += f" AND SERIAL_NUMBER = '{filter_serial}'"
query += " ORDER BY UPDATED_AT DESC"

devices_df = session.sql(query).to_pandas()

# --- Summary metrics ---
col1, col2, col3, col4 = st.columns(4)
total = len(devices_df)
col1.metric("Total Devices", total)
if total > 0:
    col2.metric("Good", len(devices_df[devices_df["CONDITION_STATUS"] == "Good"]))
    col3.metric("In Use", len(devices_df[devices_df["CURRENT_HOLDER"].notna() & (devices_df["CURRENT_HOLDER"] != "")]))
    col4.metric("Types", devices_df["DEVICE_TYPE"].nunique())

# --- Device Table ---
st.subheader(f"Devices ({total})")
if total > 0:
    display_cols = ["SERIAL_NUMBER", "DEVICE_TYPE", "VARIANT", "CONFIG", "CONDITION_STATUS",
                    "AVAILABILITY_STATUS", "BATTERY_HEALTH", "CURRENT_LOCATION", "CURRENT_HOLDER", "FIRMWARE_VERSION", "NOTES"]
    st.dataframe(devices_df[display_cols], use_container_width=True, hide_index=True)
else:
    st.info("No devices found. Add your first device below or use CSV import.")

# --- Tabs for Add / Edit / Import / Sync ---
tab_add, tab_edit, tab_import, tab_sync = st.tabs(["Add Device", "Edit Device", "Bulk Import", "Sync from DCA"])

with tab_add:
    st.subheader("Add New Device")
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
            session.sql(f"""
                INSERT INTO SCRATCH.HARDWARE_TRACKER.DEVICES 
                (SERIAL_NUMBER, DEVICE_TYPE, VARIANT, CONFIG, FIRMWARE_VERSION, 
                 BATTERY_HEALTH, CONDITION_STATUS, CURRENT_LOCATION, CURRENT_HOLDER, NOTES)
                VALUES ('{new_serial}', '{new_type}', '{new_variant}', '{new_config}', 
                        '{new_firmware}', '{new_battery}', '{new_condition}', '{new_location}', 
                        '{new_holder}', '{new_notes}')
            """).collect()
            st.success(f"Device {new_serial} added!")
            st.rerun()
        elif submitted:
            st.error("Serial Number and Device Type are required.")

with tab_edit:
    st.subheader("Edit Device")
    if total > 0:
        serial_options = devices_df["SERIAL_NUMBER"].tolist()
        selected_serial = st.selectbox("Select device to edit", serial_options, key="edit_select")
        device_row = devices_df[devices_df["SERIAL_NUMBER"] == selected_serial].iloc[0]

        with st.form("edit_device_form"):
            col_e1, col_e2 = st.columns(2)
            with col_e1:
                st.text_input("Serial Number", value=device_row["SERIAL_NUMBER"], disabled=True)
                edit_type = editable_dropdown("Device Type", "device_type", f"edit_type_{selected_serial}", current_value=device_row.get("DEVICE_TYPE", "") or "")
                edit_variant = editable_dropdown("Variant", "variant", f"edit_variant_{selected_serial}", current_value=device_row.get("VARIANT", "") or "")
                edit_config = editable_dropdown("Config", "config", f"edit_config_{selected_serial}", current_value=device_row.get("CONFIG", "") or "")
            with col_e2:
                edit_firmware = editable_dropdown("Firmware Version", "firmware_version", f"edit_firmware_{selected_serial}", current_value=device_row.get("FIRMWARE_VERSION", "") or "")
                edit_battery = editable_dropdown("Battery Health", "battery_health", f"edit_battery_{selected_serial}", current_value=device_row.get("BATTERY_HEALTH", "") or "")
                edit_condition = editable_dropdown("Condition", "condition_status", f"edit_condition_{selected_serial}", current_value=device_row.get("CONDITION_STATUS", "") or "")
                edit_location = editable_dropdown("Location", "location", f"edit_location_{selected_serial}", current_value=device_row.get("CURRENT_LOCATION", "") or "")
            edit_holder = editable_dropdown("Current Holder", "holder", f"edit_holder_{selected_serial}", current_value=device_row.get("CURRENT_HOLDER", "") or "")
            avail_options = ["Available", "Unavailable"]
            current_avail = device_row.get("AVAILABILITY_STATUS", "Available") or "Available"
            avail_index = avail_options.index(current_avail) if current_avail in avail_options else 0
            edit_availability = st.selectbox("Availability Status", avail_options, index=avail_index, key=f"edit_availability_{selected_serial}")
            edit_notes = st.text_area("Notes", value=device_row.get("NOTES", "") or "", key=f"edit_notes_{selected_serial}")
            save = st.form_submit_button("Save Changes")
            if save:
                device_id = device_row["DEVICE_ID"]
                # Track location change
                old_loc = device_row.get("CURRENT_LOCATION", "") or ""
                old_holder = device_row.get("CURRENT_HOLDER", "") or ""
                if edit_location != old_loc or edit_holder != old_holder:
                    session.sql(f"""
                        INSERT INTO SCRATCH.HARDWARE_TRACKER.LOCATION_HISTORY 
                        (DEVICE_ID, LOCATION, HOLDER, ACTION, CHANGED_BY)
                        VALUES ('{device_id}', '{edit_location}', '{edit_holder}', 'REASSIGNED', CURRENT_USER())
                    """).collect()
                session.sql(f"""
                    UPDATE SCRATCH.HARDWARE_TRACKER.DEVICES SET
                        DEVICE_TYPE = '{edit_type}',
                        VARIANT = '{edit_variant}',
                        CONFIG = '{edit_config}',
                        FIRMWARE_VERSION = '{edit_firmware}',
                        BATTERY_HEALTH = '{edit_battery}',
                        CONDITION_STATUS = '{edit_condition}',
                        AVAILABILITY_STATUS = '{edit_availability}',
                        CURRENT_LOCATION = '{edit_location}',
                        CURRENT_HOLDER = '{edit_holder}',
                        NOTES = '{edit_notes}',
                        UPDATED_AT = CURRENT_TIMESTAMP()
                    WHERE DEVICE_ID = '{device_id}'
                """).collect()
                st.success("Device updated!")
                st.rerun()

        # Study History
        with st.expander("Study History"):
            device_id = device_row["DEVICE_ID"]
            study_hist = session.sql(f"""
                SELECT s.STUDY_NAME, a.ASSIGNED_DATE, a.RETURNED_DATE, a.STATUS
                FROM SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS a
                JOIN SCRATCH.HARDWARE_TRACKER.STUDIES s ON a.STUDY_ID = s.STUDY_ID
                WHERE a.DEVICE_ID = '{device_id}'
                ORDER BY a.ASSIGNED_DATE DESC
            """).to_pandas()
            if len(study_hist) > 0:
                st.dataframe(study_hist, use_container_width=True, hide_index=True)
            else:
                st.info("No study history for this device.")
    else:
        st.info("No devices to edit.")

with tab_import:
    st.subheader("Bulk Import from CSV")
    st.markdown("Upload a CSV with columns: `SERIAL_NUMBER, DEVICE_TYPE, VARIANT, CONFIG, FIRMWARE_VERSION, BATTERY_HEALTH, CONDITION_STATUS, CURRENT_LOCATION, CURRENT_HOLDER, NOTES`")
    uploaded = st.file_uploader("Choose CSV file", type=["csv"])
    if uploaded:
        import_df = pd.read_csv(uploaded)
        st.dataframe(import_df.head(10))
        if st.button("Import All Rows"):
            for _, row in import_df.iterrows():
                vals = {col: str(row.get(col, "") or "").replace("'", "''") for col in
                        ["SERIAL_NUMBER", "DEVICE_TYPE", "VARIANT", "CONFIG", "FIRMWARE_VERSION",
                         "BATTERY_HEALTH", "CONDITION_STATUS", "CURRENT_LOCATION", "CURRENT_HOLDER", "NOTES"]}
                session.sql(f"""
                    INSERT INTO SCRATCH.HARDWARE_TRACKER.DEVICES 
                    (SERIAL_NUMBER, DEVICE_TYPE, VARIANT, CONFIG, FIRMWARE_VERSION,
                     BATTERY_HEALTH, CONDITION_STATUS, CURRENT_LOCATION, CURRENT_HOLDER, NOTES)
                    VALUES ('{vals["SERIAL_NUMBER"]}', '{vals["DEVICE_TYPE"]}', '{vals["VARIANT"]}',
                            '{vals["CONFIG"]}', '{vals["FIRMWARE_VERSION"]}', '{vals["BATTERY_HEALTH"]}',
                            '{vals["CONDITION_STATUS"]}', '{vals["CURRENT_LOCATION"]}',
                            '{vals["CURRENT_HOLDER"]}', '{vals["NOTES"]}')
                """).collect()
            st.success(f"Imported {len(import_df)} devices!")
            st.rerun()

with tab_sync:
    st.subheader("Sync from DCA Studies")
    st.markdown("Pull new devices from `data_files_metadata` that have appeared in studies but aren't yet in the tracker.")

    sync_months = st.slider("Look back (months)", min_value=1, max_value=24, value=12)

    device_families = st.multiselect(
        "Device families to sync",
        ["5.0", "MG", "Monument", "Symphony"],
        default=["5.0", "MG", "Monument", "Symphony"]
    )

    if st.button("Preview New Devices"):
        variant_filters = []
        type_map = {}
        for fam in device_families:
            if fam == "5.0":
                variant_filters.append("d.value:hardware_variant::VARCHAR ILIKE '%5.0%'")
                type_map["5.0"] = "WHOOP 5.0"
            elif fam == "MG":
                variant_filters.append("d.value:hardware_variant::VARCHAR ILIKE '%MG%'")
                type_map["MG"] = "WHOOP MG"
            elif fam == "Monument":
                variant_filters.append("d.value:hardware_variant::VARCHAR ILIKE '%monument%'")
                type_map["Monument"] = "Monument"
            elif fam == "Symphony":
                variant_filters.append("d.value:hardware_variant::VARCHAR ILIKE '%symphony%'")
                type_map["Symphony"] = "Symphony"

        variant_clause = " OR ".join(variant_filters)
        preview_query = f"""
            WITH flattened AS (
                SELECT 
                    d.value:serial::VARCHAR AS serial,
                    d.value:hardware_variant::VARCHAR AS hardware_variant,
                    d.value:hello_response:firmware_version::VARCHAR AS firmware_version,
                    d.value:hello_response:hardware_version::VARCHAR AS hardware_version,
                    ROW_NUMBER() OVER (PARTITION BY d.value:serial::VARCHAR ORDER BY m.CREATED_AT DESC) AS rn
                FROM POSTGRES_PROD.RND_DCA.DATA_FILES_METADATA m,
                    LATERAL FLATTEN(input => PARSE_JSON(m.DEVICES)) d
                WHERE m.CREATED_AT >= DATEADD('month', -{sync_months}, CURRENT_DATE())
                  AND ({variant_clause})
            )
            SELECT 
                serial AS SERIAL_NUMBER,
                hardware_variant AS VARIANT,
                firmware_version AS FIRMWARE_VERSION,
                hardware_version AS CONFIG,
                CASE
                    WHEN hardware_variant ILIKE '%5.0%' THEN 'WHOOP 5.0'
                    WHEN hardware_variant ILIKE '%MG%' THEN 'WHOOP MG'
                    WHEN hardware_variant ILIKE '%monument%' THEN 'Monument'
                    WHEN hardware_variant ILIKE '%symphony%' THEN 'Symphony'
                END AS DEVICE_TYPE
            FROM flattened
            WHERE rn = 1
              AND serial NOT IN (SELECT SERIAL_NUMBER FROM SCRATCH.HARDWARE_TRACKER.DEVICES)
            ORDER BY DEVICE_TYPE, VARIANT, SERIAL_NUMBER
        """
        new_devices_df = session.sql(preview_query).to_pandas()
        st.session_state["sync_preview"] = new_devices_df

    if "sync_preview" in st.session_state:
        new_devices_df = st.session_state["sync_preview"]
        if len(new_devices_df) == 0:
            st.success("All devices are already in the tracker. Nothing to sync.")
        else:
            st.info(f"Found **{len(new_devices_df)}** new devices not yet in the tracker.")
            st.dataframe(new_devices_df, use_container_width=True, hide_index=True)
            if st.button("Add All to Tracker"):
                for _, row in new_devices_df.iterrows():
                    serial = str(row["SERIAL_NUMBER"]).replace("'", "''")
                    dtype = str(row["DEVICE_TYPE"]).replace("'", "''")
                    variant = str(row["VARIANT"] or "").replace("'", "''")
                    firmware = str(row["FIRMWARE_VERSION"] or "").replace("'", "''")
                    config = str(row["CONFIG"] or "").replace("'", "''")
                    session.sql(f"""
                        INSERT INTO SCRATCH.HARDWARE_TRACKER.DEVICES 
                        (SERIAL_NUMBER, DEVICE_TYPE, VARIANT, CONFIG, FIRMWARE_VERSION, CONDITION_STATUS)
                        VALUES ('{serial}', '{dtype}', '{variant}', '{config}', '{firmware}', 'Active')
                    """).collect()
                st.success(f"Added {len(new_devices_df)} new devices!")
                del st.session_state["sync_preview"]
                st.rerun()
