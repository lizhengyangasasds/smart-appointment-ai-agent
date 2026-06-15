"""
Database-level custom exceptions
"""


class SlotTakenException(Exception):
    """Raised when attempting to reserve an already-busy time slot."""
    def __init__(self, technician_id: int, start_time, end_time):
        self.technician_id = technician_id
        self.start_time = start_time
        self.end_time = end_time
        super().__init__(
            f"Time slot already taken: technician_id={technician_id}, "
            f"start={start_time}, end={end_time}"
        )
