import pytest
from src.gui_dashboard import create_dashboard

def test_dashboard_layout():
    app = create_dashboard(":memory:")
    layout = app.layout
    assert any(isinstance(c, type(layout.children[0])) for c in layout.children)
