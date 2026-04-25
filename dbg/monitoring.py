import sys
from types import CodeType

MONITORING = sys.monitoring
EVENTS = sys.monitoring.events
TOOL_ID = 4  # sys.monitoring.DEBUGGER_ID
TOOL_NAME = 'PYCOFFEE_TOOL'


class Monitoring:
    """Class API to use `sys.monitoring`"""

    def __init__(self):
        self.callbacks = {}
        self.code = None

    def __call__(self, code: CodeType):
        self.code = code
        return self

    def __enter__(self):
        MONITORING.use_tool_id(TOOL_ID, TOOL_NAME)
        for event, callback in self.callbacks.items():
            MONITORING.register_callback(TOOL_ID, event, callback)
        # Entry point of the monitoring logic
        MONITORING.set_local_events(TOOL_ID, self.code, EVENTS.PY_START)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        for event in self.callbacks:
            MONITORING.register_callback(TOOL_ID, event, None)
        MONITORING.free_tool_id(TOOL_ID)
        self.code = None
        return False       

    def register_start(self, callback):
        self.callbacks[EVENTS.PY_START] = callback
