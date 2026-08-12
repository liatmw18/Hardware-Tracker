# Whoop Labs Hardware Tracker

A Streamlit web app for tracking hardware devices across studies in Whoop Labs.

## Features

- **Device Inventory** - Browse, add, edit, and filter devices with editable dropdowns
- **Scanner** - Look up devices by QR code (USB scanner) or serial number
- **Study Management** - Create studies, assign/return devices
- **Analytics** - Fleet overview, utilization metrics, device lifecycle
- **Study Planner** - Capacity planning, conflict detection, availability forecast

## Setup

### 1. Deploy to Streamlit Community Cloud

1. Push this repo to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Click "New app" and connect your GitHub repo
4. Set `app.py` as the main file
5. Deploy

### 2. Access

The app requires Snowflake credentials to log in. Only users with valid `WHOOP_LABS_ROLE` access on the `whoop-prod` account can use the tracker.

### 3. Running locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Data

All data is stored in Snowflake at `SCRATCH.HARDWARE_TRACKER`:
- `DEVICES` - Master device registry
- `STUDIES` - Study metadata
- `DEVICE_STUDY_ASSIGNMENTS` - Device-to-study links
- `LOCATION_HISTORY` - Audit trail of device movements
- `USAGE_LOGS` - Event log
- `FIELD_OPTIONS` - Dropdown values (self-expanding)
