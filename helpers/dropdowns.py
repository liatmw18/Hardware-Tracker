import streamlit as st
from snowflake.snowpark.context import get_active_session


def get_session():
    return get_active_session()


def get_field_options(field_name: str) -> list[str]:
    session = get_session()
    rows = session.sql(
        f"SELECT OPTION_VALUE FROM SCRATCH.HARDWARE_TRACKER.FIELD_OPTIONS "
        f"WHERE FIELD_NAME = '{field_name}' ORDER BY SORT_ORDER, OPTION_VALUE"
    ).collect()
    return [row["OPTION_VALUE"] for row in rows]


def add_field_option(field_name: str, option_value: str):
    session = get_session()
    session.sql(
        f"INSERT INTO SCRATCH.HARDWARE_TRACKER.FIELD_OPTIONS (FIELD_NAME, OPTION_VALUE) "
        f"SELECT '{field_name}', '{option_value}' "
        f"WHERE NOT EXISTS (SELECT 1 FROM SCRATCH.HARDWARE_TRACKER.FIELD_OPTIONS "
        f"WHERE FIELD_NAME = '{field_name}' AND OPTION_VALUE = '{option_value}')"
    ).collect()


def editable_dropdown(label: str, field_name: str, key: str, current_value: str = None, allow_none: bool = True) -> str:
    options = get_field_options(field_name)
    add_option = "+ Add new..."
    options_with_add = options + [add_option]
    if allow_none:
        options_with_add = [""] + options_with_add

    # Ensure the current value appears in the list even if not in FIELD_OPTIONS
    if current_value and current_value not in options_with_add:
        options_with_add.insert(1 if allow_none else 0, current_value)

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
