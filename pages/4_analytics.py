import streamlit as st
import pandas as pd
from snowflake.snowpark.context import get_active_session

st.set_page_config(page_title="Analytics", layout="wide")
st.title("Hardware Analytics")

session = get_active_session()

# --- Fleet Overview ---
st.header("Fleet Overview")
devices_df = session.sql("SELECT * FROM SCRATCH.HARDWARE_TRACKER.DEVICES").to_pandas()
total = len(devices_df)

if total == 0:
    st.info("No devices in the system yet. Add devices in the Inventory page to see analytics.")
    st.stop()

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Total Devices", total)
in_use = len(devices_df[devices_df["CURRENT_HOLDER"].notna() & (devices_df["CURRENT_HOLDER"] != "")])
col2.metric("In Use", in_use)
col3.metric("Available", total - in_use)
col4.metric("Device Types", devices_df["DEVICE_TYPE"].nunique())
needs_repair = len(devices_df[devices_df["CONDITION_STATUS"] == "Needs Repair"])
col5.metric("Needs Repair", needs_repair)

# --- Device Type Distribution ---
st.header("Device Distribution")
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

# --- Battery Health ---
st.header("Battery Health Distribution")
if devices_df["BATTERY_HEALTH"].notna().any():
    batt_counts = devices_df["BATTERY_HEALTH"].value_counts().reset_index()
    batt_counts.columns = ["BATTERY_HEALTH", "COUNT"]
    st.bar_chart(batt_counts, x="BATTERY_HEALTH", y="COUNT")
else:
    st.info("No battery health data recorded yet.")

# --- Utilization ---
st.header("Study Utilization")
assignments_df = session.sql("""
    SELECT d.SERIAL_NUMBER, d.DEVICE_TYPE, d.VARIANT, s.STUDY_NAME, 
           a.ASSIGNED_DATE, a.RETURNED_DATE, a.STATUS,
           DATEDIFF('day', a.ASSIGNED_DATE, COALESCE(a.RETURNED_DATE, CURRENT_DATE())) AS DAYS_IN_STUDY
    FROM SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS a
    JOIN SCRATCH.HARDWARE_TRACKER.DEVICES d ON a.DEVICE_ID = d.DEVICE_ID
    JOIN SCRATCH.HARDWARE_TRACKER.STUDIES s ON a.STUDY_ID = s.STUDY_ID
    ORDER BY a.ASSIGNED_DATE DESC
""").to_pandas()

if len(assignments_df) > 0:
    col_u1, col_u2, col_u3 = st.columns(3)
    col_u1.metric("Total Assignments", len(assignments_df))
    col_u2.metric("Avg Days per Assignment", f"{assignments_df['DAYS_IN_STUDY'].mean():.0f}")
    active_assignments = len(assignments_df[assignments_df["STATUS"].isin(["ACTIVE", "RESERVED"])])
    col_u3.metric("Active Assignments", active_assignments)

    # Devices per study
    st.subheader("Devices per Study")
    study_device_counts = assignments_df.groupby("STUDY_NAME").size().reset_index(name="DEVICE_COUNT")
    st.bar_chart(study_device_counts, x="STUDY_NAME", y="DEVICE_COUNT")

    # Most-used devices
    st.subheader("Most-Used Devices (by number of studies)")
    device_study_counts = assignments_df.groupby(["SERIAL_NUMBER", "DEVICE_TYPE"]).size().reset_index(name="STUDY_COUNT")
    device_study_counts = device_study_counts.sort_values("STUDY_COUNT", ascending=False).head(20)
    st.dataframe(device_study_counts, use_container_width=True, hide_index=True)
else:
    st.info("No study assignments recorded yet.")

# --- Device Lifecycle ---
st.header("Device Lifecycle")
lifecycle_df = devices_df[devices_df["FIRST_STUDY_DATE"].notna()].copy()
if len(lifecycle_df) > 0:
    lifecycle_df["DAYS_SINCE_FIRST_STUDY"] = (pd.Timestamp.now() - pd.to_datetime(lifecycle_df["FIRST_STUDY_DATE"])).dt.days
    st.subheader("Days Since First Study")
    st.dataframe(
        lifecycle_df[["SERIAL_NUMBER", "DEVICE_TYPE", "VARIANT", "FIRST_STUDY_DATE", "DAYS_SINCE_FIRST_STUDY", "CONDITION_STATUS"]]
        .sort_values("DAYS_SINCE_FIRST_STUDY", ascending=False).head(20),
        use_container_width=True, hide_index=True
    )
else:
    st.info("No devices have a first study date set yet.")

# --- Per-Device Drill-Down ---
st.header("Device History Drill-Down")
serial_options = devices_df["SERIAL_NUMBER"].tolist()
selected = st.selectbox("Select device", serial_options)
if selected:
    device = devices_df[devices_df["SERIAL_NUMBER"] == selected].iloc[0]
    device_id = device["DEVICE_ID"]

    st.markdown(f"**{device['DEVICE_TYPE']} {device.get('VARIANT', '')}** | Condition: {device.get('CONDITION_STATUS', 'N/A')} | Battery: {device.get('BATTERY_HEALTH', 'N/A')}")

    dcol1, dcol2 = st.columns(2)
    with dcol1:
        st.subheader("Study History")
        device_assignments = session.sql(f"""
            SELECT s.STUDY_NAME, a.STATUS, a.ASSIGNED_DATE, a.RETURNED_DATE,
                   DATEDIFF('day', a.ASSIGNED_DATE, COALESCE(a.RETURNED_DATE, CURRENT_DATE())) AS DAYS
            FROM SCRATCH.HARDWARE_TRACKER.DEVICE_STUDY_ASSIGNMENTS a
            JOIN SCRATCH.HARDWARE_TRACKER.STUDIES s ON a.STUDY_ID = s.STUDY_ID
            WHERE a.DEVICE_ID = '{device_id}'
            ORDER BY a.ASSIGNED_DATE DESC
        """).to_pandas()
        if len(device_assignments) > 0:
            st.dataframe(device_assignments, use_container_width=True, hide_index=True)
        else:
            st.info("No study history.")

    with dcol2:
        st.subheader("Location History")
        loc_history = session.sql(f"""
            SELECT LOCATION, HOLDER, ACTION, TIMESTAMP_VAL, CHANGED_BY
            FROM SCRATCH.HARDWARE_TRACKER.LOCATION_HISTORY
            WHERE DEVICE_ID = '{device_id}'
            ORDER BY TIMESTAMP_VAL DESC LIMIT 20
        """).to_pandas()
        if len(loc_history) > 0:
            st.dataframe(loc_history, use_container_width=True, hide_index=True)
        else:
            st.info("No location history.")
