import streamlit as st
from snowflake.snowpark.context import get_active_session
import sys
sys.path.insert(0, "/")
from helpers.dropdowns import editable_dropdown

st.set_page_config(page_title="Device Scanner", layout="wide")
st.title("Device Scanner")

session = get_active_session()

st.markdown("Look up a device by scanning its QR code, using a USB barcode scanner, or entering the serial number manually.")

# --- Input methods ---
tab_manual, tab_camera = st.tabs(["Serial / Scanner Input", "Camera QR Scan"])

serial_input = None

with tab_manual:
    st.markdown("**USB barcode scanners** work by typing into the focused text field below. Click the input and scan.")
    serial_input = st.text_input("Enter or scan serial number:", key="scanner_input", placeholder="Scan or type serial number...")

with tab_camera:
    st.markdown("""
    **Camera QR scanning** uses your laptop's webcam. 
    
    Note: In Streamlit-in-Snowflake, direct webcam access requires a custom component. 
    For now, use the manual input tab with a USB scanner or type the serial number.
    If you'd like camera scanning, a custom Streamlit component can be added.
    """)
    st.info("Camera QR scanning is available as an enhancement. Use the Serial/Scanner Input tab for now.")

# --- Device lookup ---
if serial_input:
    results = session.sql(f"""
        SELECT * FROM SCRATCH.HARDWARE_TRACKER.DEVICES 
        WHERE SERIAL_NUMBER = '{serial_input}' OR SERIAL_NUMBER ILIKE '%{serial_input}%'
        LIMIT 5
    """).to_pandas()

    if len(results) == 0:
        st.warning(f"No device found with serial number matching '{serial_input}'")
        st.markdown("Would you like to register this as a new device?")
        if st.button("Register New Device"):
            st.switch_page("pages/1_inventory.py")
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

            # Current study assignment
            assignments = session.sql(f"""
                SELECT s.STUDY_NAME, a.STATUS, a.ASSIGNED_DATE
                FROM SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS a
                JOIN SCRATCH.HARDWARE_TRACKER.STUDIES s ON a.STUDY_ID = s.STUDY_ID
                WHERE a.DEVICE_ID = '{device['DEVICE_ID']}' AND a.STATUS IN ('ACTIVE', 'RESERVED')
                ORDER BY a.ASSIGNED_DATE DESC
            """).to_pandas()
            if len(assignments) > 0:
                st.markdown("**Current Assignments:**")
                st.dataframe(assignments, use_container_width=True, hide_index=True)
            else:
                st.markdown("**Status:** Available (not assigned to any study)")

            # Quick actions
            st.markdown("**Quick Actions:**")
            qcol1, qcol2, qcol3 = st.columns(3)
            with qcol1:
                new_loc = editable_dropdown("Move to location", "location", f"scan_loc_{idx}")
            with qcol2:
                new_holder = editable_dropdown("Assign to person", "holder", f"scan_holder_{idx}")
            with qcol3:
                if st.button("Update Location", key=f"scan_update_{idx}"):
                    device_id = device["DEVICE_ID"]
                    session.sql(f"""
                        UPDATE SCRATCH.HARDWARE_TRACKER.DEVICES SET
                            CURRENT_LOCATION = '{new_loc}',
                            CURRENT_HOLDER = '{new_holder}',
                            UPDATED_AT = CURRENT_TIMESTAMP()
                        WHERE DEVICE_ID = '{device_id}'
                    """).collect()
                    session.sql(f"""
                        INSERT INTO SCRATCH.HARDWARE_TRACKER.LOCATION_HISTORY 
                        (DEVICE_ID, LOCATION, HOLDER, ACTION, CHANGED_BY)
                        VALUES ('{device_id}', '{new_loc}', '{new_holder}', 'SCANNED_UPDATE', CURRENT_USER())
                    """).collect()
                    st.success("Location updated!")
                    st.rerun()

            # Recent history
            with st.expander("Location History"):
                history = session.sql(f"""
                    SELECT LOCATION, HOLDER, ACTION, TIMESTAMP_VAL, CHANGED_BY
                    FROM SCRATCH.HARDWARE_TRACKER.LOCATION_HISTORY
                    WHERE DEVICE_ID = '{device['DEVICE_ID']}'
                    ORDER BY TIMESTAMP_VAL DESC LIMIT 10
                """).to_pandas()
                if len(history) > 0:
                    st.dataframe(history, use_container_width=True, hide_index=True)
                else:
                    st.info("No location history recorded yet.")
