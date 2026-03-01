from app.domain.session_flow_v2 import (
    _object_vehicle_cell_allowed,
    build_session_permissions,
    default_thread_for_role,
    visible_threads_for_role,
)


def test_default_thread_selection_matches_role_and_phase() -> None:
    assert default_thread_for_role("instructor", incident_revealed=False) == "instructor_dispatcher"
    assert default_thread_for_role("dispatcher", incident_revealed=False) == "instructor_dispatcher"
    assert default_thread_for_role("dispatcher", incident_revealed=True) == "dispatcher_rtp"
    assert default_thread_for_role("rtp", incident_revealed=True) == "dispatcher_rtp"
    assert default_thread_for_role("observer", incident_revealed=True) == "system"


def test_permissions_gate_object_access_until_unlocked() -> None:
    class Scenario:
        incident_revealed = False

    locked = build_session_permissions("rtp", Scenario())
    assert locked["can_view_object_map"] is False
    assert locked["can_control_object_vehicle"] is False

    Scenario.incident_revealed = True
    unlocked = build_session_permissions("rtp", Scenario())
    assert unlocked["can_view_object_map"] is True
    assert unlocked["can_control_object_vehicle"] is True

    dispatcher = build_session_permissions("dispatcher", Scenario())
    assert dispatcher["can_view_object_map"] is False
    assert dispatcher["can_dispatch_vehicles"] is True


def test_visible_threads_match_roles() -> None:
    assert visible_threads_for_role("instructor") == {"instructor_dispatcher", "dispatcher_rtp", "system"}
    assert visible_threads_for_role("dispatcher") == {"instructor_dispatcher", "dispatcher_rtp", "system"}
    assert visible_threads_for_role("rtp") == {"dispatcher_rtp", "system"}
    assert visible_threads_for_role("observer") == {"system"}


def test_object_vehicle_cells_allow_only_exterior_entry_or_staging_markers() -> None:
    layer_cells = {
        "walls": [0, 1, 0, 0],
        "openings": [4, 0, 0, 0],
        "markers": [0, 0, 4, 0],
    }
    assert _object_vehicle_cell_allowed(layer_cells, 0) is True
    assert _object_vehicle_cell_allowed(layer_cells, 1) is False
    assert _object_vehicle_cell_allowed(layer_cells, 2) is True
    assert _object_vehicle_cell_allowed(layer_cells, 3) is False
