from __future__ import annotations

import json

import streamlit as st

from ticker_analyzer.config import save_config
from ticker_analyzer.runtime_settings import mutation_allowed as mutation_allowed


def render_config_editor(config: dict) -> None:
    with st.expander("Scoring Settings", expanded=False):
        write_allowed = mutation_allowed("ALLOW_CONFIG_WRITE")
        st.write("Edit the JSON configuration, then save and analyze again.")
        edited = st.text_area(
            "metrics_config.json",
            value=json.dumps(config, indent=2),
            height=420,
            label_visibility="collapsed",
            disabled=not write_allowed,
        )
        cols = st.columns(2)
        if cols[0].button("Save settings", width="stretch", disabled=not write_allowed):
            try:
                parsed = json.loads(edited)
                save_config(parsed)
                st.success("Settings saved.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not save settings: {exc}")
        if cols[1].button("Reload settings", width="stretch"):
            st.rerun()
