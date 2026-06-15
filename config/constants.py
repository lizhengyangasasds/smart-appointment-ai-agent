from enum import Enum
busy_periods_dict = {}  # { technician_id: [ {"start": "...", "end": "..."} ] }

class StateEnum(Enum):
    CLASSIFY = "classify"
    APPOINTMENT = "appointment"
    CONSULT = "consult"
    OTHER = "other"
    
class SharedState:
    def __init__(self):
        self.value = StateEnum.CLASSIFY
