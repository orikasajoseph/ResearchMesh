"""Single verified-PDF Reader Group handler; never substitutes abstract analysis."""
from aip.group_analysis_handler import AnalysisGroupHandler


class ReaderGroupHandler(AnalysisGroupHandler):
    task_type = "paper_read"
    output_schema = "paper_notes.v1"
    prefix = "READER"
    product_name = "paper-notes-v1"
    minimum_timeout = 660

    def __init__(self, *args, backend_timeout_seconds=690, **kwargs):
        super().__init__(*args, backend_timeout_seconds=backend_timeout_seconds, **kwargs)
