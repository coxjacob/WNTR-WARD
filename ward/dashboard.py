import os
import time
from typing import Any

import httpx
import plotly.graph_objects as go
import streamlit as st

DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_PRESSURE_THRESHOLD_PSI = 20.0
POLL_INTERVAL_SEC = 0.5
MAX_AUTO_POLLS = 60


def get_api_base_url() -> str:
    return os.environ.get("WNTR_API_URL", DEFAULT_API_URL).rstrip("/")


def api_get(client: httpx.Client, path: str) -> dict[str, Any] | list[Any]:
    response = client.get(path)
    if response.status_code >= 400:
        detail = _extract_api_error(response)
        raise RuntimeError(detail)
    return response.json()


def api_post(
    client: httpx.Client, path: str, payload: dict[str, Any]
) -> dict[str, Any]:
    response = client.post(path, json=payload)
    if response.status_code >= 400:
        detail = _extract_api_error(response)
        raise RuntimeError(detail)
    return response.json()


def _extract_api_error(response: httpx.Response) -> str:
    try:
        body = response.json()
        if isinstance(body, dict) and "error" in body:
            err = body["error"]
            return f"{err.get('code', 'API_ERROR')}: {err.get('message', 'Request failed.')}"
    except ValueError:
        pass
    return f"HTTP {response.status_code}: {response.reason_phrase}"


def _format_scenario_option(scenario: dict[str, Any]) -> str:
    return f"{scenario['scenario_id']} — {scenario.get('name', 'Unnamed scenario')}"


def _extract_affected_map(analysis: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not analysis or not isinstance(analysis, dict):
        return {}
    affected_list = analysis.get("affected_nodes") or []
    return {
        str(item["node_id"]): item
        for item in affected_list
        if isinstance(item, dict) and "node_id" in item
    }


def _extract_pressure_threshold(analysis: dict[str, Any] | None) -> float:
    if isinstance(analysis, dict):
        thresholds = analysis.get("thresholds")
        if isinstance(thresholds, dict) and "min_pressure_psi" in thresholds:
            return float(thresholds["min_pressure_psi"])
    return DEFAULT_PRESSURE_THRESHOLD_PSI


def _window_hours(timing: dict[str, Any] | None) -> tuple[float, float]:
    if not timing:
        return 0.0, 0.0
    start_hr = float(timing.get("start_time_sec", 0.0)) / 3600.0
    end_hr = start_hr + float(timing.get("duration_sec", 0.0)) / 3600.0
    return start_hr, end_hr


def build_network_figure(
    topology: dict[str, Any],
    *,
    view_mode: str,
    scenario: dict[str, Any] | None = None,
    results: dict[str, Any] | None = None,
    selected_node: str | None = None,
) -> go.Figure | None:
    """Build an interactive Plotly network map for 'baseline' or 'attack' view.

    Returns None if the network lacks usable coordinates.
    """
    nodes = topology.get("nodes", [])
    links = topology.get("links", [])
    coord_map: dict[str, tuple[float, float]] = {}
    missing_coords = 0

    for node in nodes:
        coords = node.get("coordinates")
        if (
            isinstance(coords, (list, tuple))
            and len(coords) >= 2
            and coords[0] is not None
            and coords[1] is not None
        ):
            coord_map[str(node["node_id"])] = (float(coords[0]), float(coords[1]))
        else:
            missing_coords += 1

    if not coord_map:
        return None

    targeted_ids = {
        str(c) for c in (scenario.get("target_components", []) if scenario else [])
    }
    analysis = (results or {}).get("analysis") or {}
    affected_map = _extract_affected_map(analysis)
    affected_ids = set(affected_map.keys()) if view_mode == "attack" else set()
    pressures_by_case = ((results or {}).get("pressures") or {}).get(view_mode) or {}

    timing = (results or {}).get("timing") or (scenario or {}).get("timing")
    start_hr, end_hr = _window_hours(timing)
    coord_sys = (topology.get("visualization") or {}).get("coordinate_system", "model")

    figure = go.Figure()

    # 1. Pipes first :)
    pipe_x: list[float | None] = []
    pipe_y: list[float | None] = []
    pump_x: list[float] = []
    pump_y: list[float] = []
    pump_text: list[str] = []
    pump_ids: list[str] = []
    attacked_pump_x: list[float] = []
    attacked_pump_y: list[float] = []
    attacked_pump_text: list[str] = []
    attacked_pump_ids: list[str] = []

    for link in links:
        link_id = str(link["link_id"])
        link_type = str(link.get("type", "Pipe"))
        start_c = coord_map.get(str(link.get("start_node")))
        end_c = coord_map.get(str(link.get("end_node")))
        if start_c is None or end_c is None:
            continue

        vertices = [
            (float(v[0]), float(v[1]))
            for v in (link.get("vertices") or [])
            if isinstance(v, (list, tuple)) and len(v) >= 2
        ]
        path = [start_c, *vertices, end_c]

        if link_type.lower() == "pump":
            mid_x = (start_c[0] + end_c[0]) / 2.0
            mid_y = (start_c[1] + end_c[1]) / 2.0
            is_attacked = view_mode == "attack" and link_id in targeted_ids
            status_label = "ATTACKED (OFF)" if is_attacked else "Normal operation"
            hover = (
                f"Pump: {link_id}<br>"
                f"From {link['start_node']} to {link['end_node']}<br>"
                f"Status ({view_mode}): {status_label}"
            )
            if is_attacked:
                attacked_pump_x.append(mid_x)
                attacked_pump_y.append(mid_y)
                attacked_pump_text.append(hover)
                attacked_pump_ids.append(link_id)
            else:
                pump_x.append(mid_x)
                pump_y.append(mid_y)
                pump_text.append(hover)
                pump_ids.append(link_id)
        else:
            for px, py in path:
                pipe_x.append(px)
                pipe_y.append(py)
            pipe_x.append(None)
            pipe_y.append(None)

    if pipe_x:
        figure.add_trace(
            go.Scatter(
                x=pipe_x,
                y=pipe_y,
                mode="lines",
                name="Pipes",
                line={"color": "#94a3b8", "width": 1.5},
                hoverinfo="skip",
            )
        )

    # 2. Categorize nodes in these: Unaffected Junctions, Affected Junctions, Tanks/Reservoirs
    unaffected_j_x, unaffected_j_y, unaffected_j_text, unaffected_j_ids = [], [], [], []
    affected_j_x, affected_j_y, affected_j_text, affected_j_ids = [], [], [], []
    tank_x, tank_y, tank_text, tank_ids = [], [], [], []

    for node in nodes:
        node_id = str(node["node_id"])
        if node_id not in coord_map:
            continue
        nx, ny = coord_map[node_id]
        node_type = str(node.get("type", "Junction"))

        series = pressures_by_case.get(node_id) or []
        valid_vals = [float(v) for v in series if v is not None]
        min_psi_str = f"{min(valid_vals):.2f} psi" if valid_vals else "N/A"

        if node_type.lower() in {"tank", "reservoir"}:
            tank_x.append(nx)
            tank_y.append(ny)
            tank_ids.append(node_id)
            tank_text.append(
                f"{node_type}: {node_id}<br>"
                f"Coordinates ({coord_sys}): ({nx:.1f}, {ny:.1f})<br>"
                f"Min pressure ({view_mode}): {min_psi_str}"
            )
        elif node_id in affected_ids:
            info = affected_map[node_id]
            affected_j_x.append(nx)
            affected_j_y.append(ny)
            affected_j_ids.append(node_id)
            affected_j_text.append(
                f"Affected Junction: {node_id} (Rank #{info.get('rank', '-')})<br>"
                f"Attack min pressure: {info.get('attack_min_pressure_psi', min_psi_str)} psi<br>"
                f"Max drop: {info.get('max_pressure_drop_psi', 'N/A')} psi<br>"
                f"Impact: {info.get('impact_category', 'affected')}"
            )
        else:
            unaffected_j_x.append(nx)
            unaffected_j_y.append(ny)
            unaffected_j_ids.append(node_id)
            unaffected_j_text.append(
                f"Junction: {node_id}<br>"
                f"Status ({view_mode}): Unaffected<br>"
                f"Min pressure ({view_mode}): {min_psi_str}"
            )

    if unaffected_j_x:
        figure.add_trace(
            go.Scatter(
                x=unaffected_j_x,
                y=unaffected_j_y,
                mode="markers",
                name="Unaffected junctions",
                customdata=unaffected_j_ids,
                text=unaffected_j_text,
                hovertemplate="%{text}<extra></extra>",
                marker={"size": 6, "color": "#2563eb", "opacity": 0.8},
            )
        )

    if affected_j_x:
        figure.add_trace(
            go.Scatter(
                x=affected_j_x,
                y=affected_j_y,
                mode="markers",
                name="Affected nodes",
                customdata=affected_j_ids,
                text=affected_j_text,
                hovertemplate="%{text}<extra></extra>",
                marker={
                    "size": 11,
                    "color": "#dc2626",
                    "line": {"width": 1.5, "color": "#7f1d1d"},
                },
            )
        )

    if tank_x:
        figure.add_trace(
            go.Scatter(
                x=tank_x,
                y=tank_y,
                mode="markers+text",
                name="Tanks / Reservoirs",
                customdata=tank_ids,
                text=tank_ids,
                textposition="top center",
                textfont={"size": 9},
                hovertext=tank_text,
                hovertemplate="%{hovertext}<extra></extra>",
                marker={
                    "size": 12,
                    "symbol": "square",
                    "color": "#0d9488",
                    "line": {"width": 1.5, "color": "#115e59"},
                },
            )
        )

    if pump_x:
        figure.add_trace(
            go.Scatter(
                x=pump_x,
                y=pump_y,
                mode="markers+text",
                name="Pumps (normal)",
                customdata=pump_ids,
                text=pump_ids,
                textposition="bottom center",
                textfont={"size": 9},
                hovertext=pump_text,
                hovertemplate="%{hovertext}<extra></extra>",
                marker={
                    "size": 12,
                    "symbol": "diamond",
                    "color": "#16a34a",
                    "line": {"width": 1.5, "color": "#14532d"},
                },
            )
        )

    if attacked_pump_x:
        figure.add_trace(
            go.Scatter(
                x=attacked_pump_x,
                y=attacked_pump_y,
                mode="markers+text",
                name="Attacked pump",
                customdata=attacked_pump_ids,
                text=attacked_pump_ids,
                textposition="bottom center",
                textfont={"size": 10, "color": "#b91c1c"},
                hovertext=attacked_pump_text,
                hovertemplate="%{hovertext}<extra></extra>",
                marker={
                    "size": 15,
                    "symbol": "diamond",
                    "color": "#f97316",
                    "line": {"width": 2.5, "color": "#991b1b"},
                },
            )
        )

    if selected_node and selected_node in coord_map:
        sx, sy = coord_map[selected_node]
        figure.add_trace(
            go.Scatter(
                x=[sx],
                y=[sy],
                mode="markers",
                name=f"Selected node ({selected_node})",
                customdata=[selected_node],
                hovertemplate=f"Selected node: {selected_node}<extra></extra>",
                marker={
                    "size": 16,
                    "symbol": "circle-open",
                    "color": "#eab308",
                    "line": {"width": 3, "color": "#ca8a04"},
                },
            )
        )

    window_note = (
        f"Attack window: {start_hr:.2f}–{end_hr:.2f} h"
        if timing
        else "Simulation window: full run"
    )
    missing_note = (
        f" ({missing_coords} nodes without coordinates omitted)"
        if missing_coords
        else ""
    )

    figure.update_layout(
        title={
            "text": (
                f"{view_mode.capitalize()} Network View — {window_note}<br>"
                f"<sup>Coordinates: {coord_sys} units | Pressures: psi{missing_note}</sup>"
            ),
            "font": {"size": 14},
        },
        xaxis={
            "title": f"X ({coord_sys} units)",
            "showgrid": False,
            "zeroline": False,
        },
        yaxis={
            "title": f"Y ({coord_sys} units)",
            "showgrid": False,
            "zeroline": False,
            "scaleanchor": "x",
            "scaleratio": 1,
        },
        height=440,
        margin={"t": 65, "b": 40, "l": 40, "r": 20},
        legend={"orientation": "h", "y": -0.22},
        clickmode="event+select",
    )
    return figure


def build_pressure_figure(
    results: dict[str, Any],
    selected_node: str,
) -> go.Figure:
    """Plot baseline and attack pressure series for a selected node on a shared time axis."""
    time_sec = results.get("time_sec") or []
    time_hours = [float(t) / 3600.0 for t in time_sec]
    pressures = results.get("pressures") or {}
    baseline_series = (pressures.get("baseline") or {}).get(selected_node) or []
    attack_series = (pressures.get("attack") or {}).get(selected_node) or []
    units = results.get("units") or {"time": "s", "pressure": "psi"}
    pressure_unit = units.get("pressure", "psi")

    timing = results.get("timing") or {}
    start_hr, end_hr = _window_hours(timing)
    threshold_psi = _extract_pressure_threshold(results.get("analysis"))

    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=time_hours,
            y=baseline_series,
            name=f"Baseline ({pressure_unit})",
            mode="lines+markers",
            line={"color": "#2563eb", "width": 2},
            connectgaps=False,
        )
    )
    figure.add_trace(
        go.Scatter(
            x=time_hours,
            y=attack_series,
            name=f"Attack ({pressure_unit})",
            mode="lines+markers",
            line={"color": "#dc2626", "width": 2, "dash": "dash"},
            connectgaps=False,
        )
    )

    if end_hr > start_hr:
        figure.add_vrect(
            x0=start_hr,
            x1=end_hr,
            fillcolor="#f97316",
            opacity=0.14,
            line_width=0,
            annotation_text="Attack interval",
            annotation_position="top left",
        )

    figure.add_hline(
        y=threshold_psi,
        line_dash="dot",
        line_color="#7c2d12",
        annotation_text=f"Min pressure threshold ({threshold_psi:.1f} {pressure_unit})",
        annotation_position="bottom right",
    )

    figure.update_layout(
        title=f"Node {selected_node} — Baseline vs. Attack Pressure ({pressure_unit})",
        xaxis_title="Simulation Time (hours)",
        yaxis_title=f"Pressure ({pressure_unit})",
        height=380,
        margin={"t": 50, "b": 70, "l": 50, "r": 20},
        legend={"orientation": "h", "y": -0.25},
    )
    return figure


def _render_run_status_banner(run_status: dict[str, Any]) -> None:
    status = run_status.get("status", "unknown")
    run_id = run_status.get("run_id", "")
    if status == "queued":
        st.info(f"Run `{run_id}` status: **queued** — waiting for simulation worker.")
    elif status == "running":
        st.info(
            f"Run `{run_id}` status: **running** — executing baseline and attack simulations."
        )
    elif status == "succeeded":
        st.success(f"Run `{run_id}` status: **succeeded**.")
    elif status == "failed":
        err = run_status.get("error") or {}
        code = err.get("code", "RUN_FAILED")
        msg = err.get("message", "The simulation run failed.")
        st.error(f"Run `{run_id}` status: **failed** ({code}) — {msg}")
    else:
        st.warning(f"Run `{run_id}` status: **{status}**.")


def _render_metadata_panel(
    scenario: dict[str, Any],
    run_status: dict[str, Any] | None,
    results: dict[str, Any] | None,
) -> None:
    st.subheader("Scenario & Simulation Metadata")
    targets = ", ".join(scenario.get("target_components") or []) or "None"
    timing = (results or {}).get("timing") or scenario.get("timing") or {}
    start_hr, end_hr = _window_hours(timing)
    timestamp = (
        (results or {}).get("completed_at")
        or (run_status or {}).get("completed_at")
        or (results or {}).get("created_at")
        or (run_status or {}).get("created_at")
        or "Not yet completed"
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Scenario ID", scenario.get("scenario_id", "—"))
    c2.metric("Attack Type", scenario.get("attack_type", "—"))
    c3.metric("Targeted Components", targets)
    c4.metric("Simulation Timestamp", str(timestamp))

    st.caption(
        f"**Name:** {scenario.get('name', '—')} | "
        f"**Description:** {scenario.get('description') or 'No description provided.'} | "
        f"**Attack Window:** {start_hr:.2f} h to {end_hr:.2f} h"
    )


def _sync_node_from_plot_selection(event: Any, valid_nodes: list[str]) -> None:
    if not event or not isinstance(event, dict):
        return
    selection = event.get("selection") or {}
    points = selection.get("points") or []
    if not points:
        return
    custom = points[0].get("customdata")
    if isinstance(custom, list) and custom:
        candidate = str(custom[0])
    elif custom is not None:
        candidate = str(custom)
    else:
        return
    if candidate in valid_nodes and st.session_state.get("selected_node") != candidate:
        st.session_state["selected_node"] = candidate


def main() -> None:
    st.set_page_config(page_title="WARD Attack Impact Dashboard", layout="wide")
    st.title("WARD — Water Network Cyber-Attack Demo Dashboard")

    api_url = get_api_base_url()

    with st.sidebar:
        st.header("Navigation")
        page = st.radio(
            "View",
            options=["Scenario Run & Overview", "Network & Pressure Explorer"],
            index=0,
        )
        st.divider()
        st.caption(f"Connected API: `{api_url}`")
        if st.button("Reload API Data"):
            st.rerun()

    try:
        with httpx.Client(base_url=api_url, timeout=10.0) as client:
            scenarios = api_get(client, "/scenarios")
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        st.error(
            f"Cannot connect to WARD FastAPI service at {api_url}. "
            f"Verify the API server is running and reload. ({exc})"
        )
        st.stop()

    if not isinstance(scenarios, list) or not scenarios:
        st.warning(
            "No scenarios are available in the API. Create or preload a scenario first."
        )
        st.stop()

    scenario_by_label = {_format_scenario_option(s): s for s in scenarios}
    selected_label = st.selectbox(
        "Select Scenario",
        options=list(scenario_by_label.keys()),
        key="scenario_selector",
    )
    selected_scenario = scenario_by_label[selected_label]
    scenario_id = selected_scenario["scenario_id"]
    model_id = selected_scenario["model_id"]

    # Reset active run state if the operator switches scenarios
    if st.session_state.get("active_scenario_id") != scenario_id:
        st.session_state["active_scenario_id"] = scenario_id
        st.session_state.pop("active_run_id", None)
        st.session_state.pop("selected_node", None)

    action_col, poll_col = st.columns([1, 1])
    with action_col:
        run_clicked = st.button("Run Scenario", type="primary")
    with poll_col:
        refresh_clicked = st.button("Refresh Run Status")

    try:
        with httpx.Client(base_url=api_url, timeout=10.0) as client:
            if run_clicked:
                submitted = api_post(client, "/runs", {"scenario_id": scenario_id})
                st.session_state["active_run_id"] = submitted["run_id"]

            run_status: dict[str, Any] | None = None
            results: dict[str, Any] | None = None
            active_run_id = st.session_state.get("active_run_id")

            if active_run_id:
                run_status = api_get(client, f"/runs/{active_run_id}")
                polls = 0
                while (
                    run_status.get("status") in {"queued", "running"}
                    and polls < MAX_AUTO_POLLS
                ):
                    time.sleep(POLL_INTERVAL_SEC)
                    run_status = api_get(client, f"/runs/{active_run_id}")
                    polls += 1

                if run_status.get("status") == "succeeded":
                    results = api_get(client, f"/runs/{active_run_id}/results")

            topology = api_get(client, f"/models/{model_id}/topology")
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        st.error(f"API error while loading run or network data: {exc}")
        st.stop()

    if refresh_clicked and not st.session_state.get("active_run_id"):
        st.info("No active run yet. Click **Run Scenario** to start a simulation.")

    if run_status is not None:
        _render_run_status_banner(run_status)
    else:
        st.info(
            "Select a scenario above and click **Run Scenario** to execute paired baseline and attack simulations."
        )

    _render_metadata_panel(selected_scenario, run_status, results)
    st.divider()

    # Build node options synchronized between netwokr maps and timeseries selector
    topology_nodes = [str(n["node_id"]) for n in topology.get("nodes", [])]
    affected_map = _extract_affected_map((results or {}).get("analysis"))
    affected_ids = list(affected_map.keys())

    if results is not None:
        summary = (results.get("analysis") or {}).get("summary") or {}
        st.subheader("Impact Analysis Summary")
        m1, m2, m3 = st.columns(3)
        m1.metric(
            "Evaluated Nodes", summary.get("total_evaluated_nodes", len(topology_nodes))
        )
        m2.metric(
            "Affected Nodes", summary.get("affected_node_count", len(affected_ids))
        )
        m3.metric(
            "Pre-existing Low Pressure Only",
            summary.get("pre_existing_only_node_count", 0),
        )
        if not affected_ids:
            st.info(
                "No nodes exceeded the attack impact thresholds for this scenario. "
                "You can still inspect any node's baseline vs. attack pressure curve below."
            )

    filter_affected_only = False
    if affected_ids:
        filter_affected_only = st.checkbox(
            f"Filter node selector to affected nodes ({len(affected_ids)})",
            value=False,
        )

    selectable_nodes = (
        affected_ids if (filter_affected_only and affected_ids) else topology_nodes
    )
    if not selectable_nodes:
        st.warning("No network nodes are available in the model topology.")
        st.stop()

    default_node = (
        affected_ids[0]
        if affected_ids and affected_ids[0] in selectable_nodes
        else selectable_nodes[0]
    )
    if (
        results is not None
        and affected_ids
        and st.session_state.get("focused_run_id") != active_run_id
    ):
        st.session_state["focused_run_id"] = active_run_id
        st.session_state["selected_node"] = affected_ids[0]
    elif st.session_state.get("selected_node") not in selectable_nodes:
        st.session_state["selected_node"] = default_node

    selected_node = st.selectbox(
        "Inspect Node (synchronized with network maps)",
        options=selectable_nodes,
        key="selected_node",
    )

    if page == "Scenario Run & Overview":
        st.subheader("Baseline vs. Attack Network Views")
    else:
        st.subheader("Detailed Network & Pressure Explorer")

    base_col, atk_col = st.columns(2)
    with base_col:
        base_fig = build_network_figure(
            topology,
            view_mode="baseline",
            scenario=selected_scenario,
            results=results,
            selected_node=selected_node,
        )
        if base_fig is None:
            st.warning(
                "Network coordinates are missing for this model, so the baseline map cannot be drawn."
            )
        else:
            base_event = st.plotly_chart(
                base_fig,
                width="stretch",
                on_select="rerun",
                key="baseline_network_plot",
            )
            _sync_node_from_plot_selection(base_event, selectable_nodes)

    with atk_col:
        atk_fig = build_network_figure(
            topology,
            view_mode="attack",
            scenario=selected_scenario,
            results=results,
            selected_node=selected_node,
        )
        if atk_fig is None:
            st.warning(
                "Network coordinates are missing for this model, so the attack map cannot be drawn."
            )
        else:
            atk_event = st.plotly_chart(
                atk_fig,
                width="stretch",
                on_select="rerun",
                key="attack_network_plot",
            )
            _sync_node_from_plot_selection(atk_event, selectable_nodes)

    st.divider()
    st.subheader(f"Pressure Over Time — Node {selected_node}")

    if run_status is None:
        st.info(
            "Run the scenario to generate baseline and attack pressure time series."
        )
    elif run_status.get("status") in {"queued", "running"}:
        st.info(
            "Simulation results are not ready yet while the run is still in progress."
        )
    elif run_status.get("status") == "failed":
        st.warning(
            "No pressure results are available because this simulation run failed."
        )
    elif results is None:
        st.warning("Results payload is missing for this completed run.")
    else:
        pressures = results.get("pressures") or {}
        base_vals = (pressures.get("baseline") or {}).get(selected_node)
        atk_vals = (pressures.get("attack") or {}).get(selected_node)

        if not results.get("time_sec") or base_vals is None or atk_vals is None:
            st.warning(
                f"Pressure measurements are missing for node `{selected_node}` in the saved results."
            )
        else:
            if selected_node in affected_map:
                node_impact = affected_map[selected_node]
                reasons = "; ".join(node_impact.get("reasons") or [])
                st.warning(
                    f"Node **{selected_node}** is **affected** (Rank #{node_impact.get('rank', '-')}, "
                    f"max drop {node_impact.get('max_pressure_drop_psi', 'N/A')} psi). {reasons}"
                )
            else:
                st.info(
                    f"Node **{selected_node}** is **unaffected** under the active impact thresholds."
                )

            pressure_fig = build_pressure_figure(results, selected_node)
            st.plotly_chart(pressure_fig, width="stretch")

            time_hours = [round(float(t) / 3600.0, 3) for t in results["time_sec"]]
            st.dataframe(
                {
                    "Time (hours)": time_hours,
                    "Time (s)": results["time_sec"],
                    "Baseline (psi)": base_vals,
                    "Attack (psi)": atk_vals,
                },
                hide_index=True,
            )


if __name__ == "__main__":
    main()
